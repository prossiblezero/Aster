"""Token-in, token-out rollouts of a local policy through AsterEnv.

The trainer needs, for every assistant turn, the exact prompt token ids the policy saw, the
token ids it generated, and their log-probabilities under the behavior policy. Decoding and
re-encoding text would silently change those tokens, so this module:

- renders each request with the policy's own chat template (``tokenizer``),
- samples token ids from a generation backend (``generate``; see ``VLLMCompletions``),
- parses the decoded completion itself (``qwen.parse_completion``),
- records one ``Turn`` per decision and splits the episode into segments wherever the next
  prompt is not an exact extension of the previous prompt plus completion (context
  compaction, archived observations, or a chat template that rewrites earlier turns).

Per-turn samples are always exact. Segments say which turns can be concatenated into one
sequence. Episode labels keep infrastructure failures out of the gradient.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

from ..config import Settings
from ..providers.codec import request_payload
from ..schema import Final, TaskSpec, ToolCall
from .env import AsterEnv
from .qwen import parse_completion


def training_settings(**overrides) -> Settings:
    """Product behaviour that would leak into the training distribution is off.

    No memory or background maintenance, no workers, no approval prompts, no completion gate
    (the reward comes from an independent verifier) and no workflow-state notes. Chat
    Completions without streaming, which local OpenAI-compatible servers implement.
    """
    defaults = dict(
        protocol="chat", stream=False, auth="none", approval="auto", completion="off",
        memory_enabled=False, memory_evolution=False, auto_dream=False, max_workers=0,
        workflow_notes=False, max_recovery=0, oneshot_maintenance_seconds=0,
    )
    return Settings(**{**defaults, **overrides})


class Tokenizer(Protocol):
    def apply_chat_template(self, conversation, *, tools=None, add_generation_prompt=False,
                            tokenize=True, **kwargs) -> list[int]: ...

    def decode(self, token_ids: list[int], **kwargs) -> str: ...


@dataclass
class Generation:
    token_ids: list[int]
    logprobs: list[float]
    finish_reason: str  # "stop" or "length"


Generate = Callable[[list[int]], Awaitable[Generation]]


@dataclass
class Turn:
    prompt_ids: list[int]
    completion_ids: list[int]
    logprobs: list[float]
    finish_reason: str
    segment: int
    extends_previous: bool
    calls: list[dict] = field(default_factory=list)
    parse_errors: list[str] = field(default_factory=list)
    context_compacted: bool = False

    @property
    def loss_mask(self) -> list[bool]:
        return [False] * len(self.prompt_ids) + [True] * len(self.completion_ids)


@dataclass
class Trajectory:
    task_id: str
    turns: list[Turn]
    reward: float | None
    label: str  # success, candidate_failure, budget_exhausted, parse_error, infrastructure_error
    trainable: bool
    info: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)


def chat_conversation(model_input: list[dict], tools: list[dict]) -> tuple[list[dict], list[dict]]:
    """Aster's request as chat messages and tools, with arguments as objects for templates."""
    payload = request_payload(model_input, tools, protocol="chat")
    messages = json.loads(json.dumps(payload["messages"]))
    for message in messages:
        for call in message.get("tool_calls") or []:
            arguments = call.get("function", {}).get("arguments")
            if isinstance(arguments, str):
                try:
                    call["function"]["arguments"] = json.loads(arguments)
                except ValueError:
                    pass
    return messages, payload["tools"]


class VLLMCompletions:
    """vLLM's OpenAI-compatible /v1/completions with a token-id prompt.

    ``return_tokens_as_token_ids`` makes vLLM report each sampled token as ``token_id:N``,
    so the ids are read back exactly instead of being re-encoded from text.
    """

    def __init__(self, base_url: str, model: str, *, max_tokens: int = 4096, temperature: float = 1.0,
                 top_p: float = 1.0, seed: int | None = None, client=None, timeout: float = 600):
        import httpx

        self.url = base_url.rstrip("/") + "/completions"
        self.body = {"model": model, "max_tokens": max_tokens, "temperature": temperature, "top_p": top_p,
                     "logprobs": 0, "return_tokens_as_token_ids": True, "skip_special_tokens": False}
        if seed is not None:
            self.body["seed"] = seed
        self.client = client or httpx.AsyncClient(timeout=timeout)

    async def __call__(self, prompt_ids: list[int]) -> Generation:
        response = await self.client.post(self.url, json={**self.body, "prompt": prompt_ids})
        response.raise_for_status()
        choice = response.json()["choices"][0]
        tokens = choice["logprobs"]["tokens"]
        ids = [int(token.removeprefix("token_id:")) for token in tokens]
        logprobs = [float(value) for value in choice["logprobs"]["token_logprobs"]]
        if len(ids) != len(logprobs):
            raise ValueError("token ids and logprobs differ in length")
        return Generation(ids, logprobs, choice.get("finish_reason") or "stop")


async def rollout(env: AsterEnv, task: TaskSpec | str, tokenizer: Tokenizer, generate: Generate, *,
                  task_id: str = "", template_kwargs: dict | None = None) -> Trajectory:
    """Run one episode; never raises for policy mistakes, only labels them."""
    observation, info = env.reset(task)
    turns: list[Turn] = []
    segment, previous = 0, None
    reward, terminated, truncated = None, False, False
    label = "budget_exhausted"
    try:
        while True:
            messages, tools = chat_conversation(observation["model_input"], observation["tools"])
            prompt_ids = list(tokenizer.apply_chat_template(
                messages, tools=tools, add_generation_prompt=True, tokenize=True, **(template_kwargs or {})))
            extends = previous is not None and prompt_ids[:len(previous)] == previous
            if previous is not None and not extends:
                segment += 1
            generation = await generate(prompt_ids)
            text = tokenizer.decode(generation.token_ids, skip_special_tokens=False)
            parsed = parse_completion(text, tools)
            if generation.finish_reason == "length":
                parsed.errors.append("the output reached the token limit before finishing")
            turn = Turn(prompt_ids, generation.token_ids, generation.logprobs, generation.finish_reason,
                        segment, extends, [{"name": n, "arguments": a} for n, a in parsed.calls],
                        list(parsed.errors), bool(observation.get("context_compacted")))
            turns.append(turn)
            previous = prompt_ids + generation.token_ids
            if parsed.errors:
                result = await env.step_invalid(text, "; ".join(parsed.errors))
            elif parsed.calls:
                result = await env.step_turn(parsed.text, [
                    ToolCall(call_id=f"call_{len(turns)}_{i}", name=n, arguments=a)
                    for i, (n, a) in enumerate(parsed.calls)])
            else:
                result = await env.step(Final(answer=parsed.text))
            observation, reward, terminated, truncated, step_info = result
            if terminated or truncated:
                info.update(step_info)
                break
        verified = (info.get("reward_verification") or {}).get("passed")
        if turns and turns[-1].parse_errors and not verified:
            label = "parse_error"
        elif verified:
            label = "success"
        elif terminated:
            label = "candidate_failure"
    except Exception as exc:  # generation server, sandbox or verifier crashed: not the policy's fault
        info["error"] = f"{type(exc).__name__}: {exc}"
        label, reward = "infrastructure_error", None
    finally:
        try:
            env.close()
        except RuntimeError:
            pass
    trainable = label in {"success", "candidate_failure", "parse_error"} and reward is not None
    return Trajectory(task_id or str(info.get("run_id", "")), turns, reward, label, trainable,
                      {key: info.get(key) for key in ("run_id", "stop_reason", "error", "reward_verification")})
