"""Offline training data contracts, without tokenizers, sampling or optimization."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Annotated, Any, Literal

from pydantic import Field, FiniteFloat, StrictBool, StrictInt, model_validator

from .history import canonicalize
from .schema import Record, ToolCall
from .workspace import digest

Hash = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Revision = Annotated[str, Field(pattern=r"^(?:[a-f0-9]{40}|[a-f0-9]{64})$")]
Name = Annotated[str, Field(min_length=1)]
Token = Annotated[StrictInt, Field(ge=0)]


class TaskRecord(Record):
    task_id: Name
    repository: Name
    repository_revision: Revision
    issue_family: Name
    source: Name
    license: Name
    training_allowed: StrictBool
    origin: Literal["new", "existing_evaluation", "existing_practice"]
    split: Literal["train", "dev", "test", "audit"]
    fixture_sha256: Hash
    environment_sha256: Hash
    verifier_sha256: Hash

    @model_validator(mode="after")
    def admissible(self):
        if self.origin != "new" and self.split != "audit":
            raise ValueError("existing evaluation/practice evidence is audit-only")
        if self.split == "train" and not self.training_allowed:
            raise ValueError("training requires an explicit license admission")
        return self


def validate_splits(tasks: list[TaskRecord]) -> None:
    """The same repository or issue family cannot cross the frozen splits."""
    identifiers, repositories, families = set(), {}, {}
    for task in tasks:
        if task.task_id in identifiers:
            raise ValueError("duplicate task id")
        identifiers.add(task.task_id)
        for key, assignments in ((task.repository, repositories), (task.issue_family, families)):
            if key in assignments and assignments[key] != task.split:
                raise ValueError("repository or issue-family split leakage")
            assignments[key] = task.split


class SFTRecord(Record):
    task: TaskRecord
    run_id: Name
    turn: int = Field(ge=1)
    request_sha256: Hash
    completion_event_sha256: Hash
    provider: Name
    protocol: Literal["responses", "chat", "anthropic"]
    teacher_model: Name
    context_policy_version: Name
    prompt_messages: list[dict[str, Any]]
    source_tools: list[dict[str, Any]]
    assistant_target: list[dict[str, Any]] = Field(min_length=1)
    omitted_prompt_reasoning: int = Field(ge=0)


def _event_hash(event: dict) -> str:
    body = {key: value for key, value in event.items() if key != "hash"}
    expected = digest(json.dumps(body, sort_keys=True, ensure_ascii=False))
    if event.get("hash") != expected:
        raise ValueError("event hash mismatch")
    return expected


def prepare_sft(
    artifact: dict,
    context_event: dict,
    reply_event: dict,
    *,
    task: TaskRecord,
    provider: str,
    protocol: Literal["responses", "chat", "anthropic"],
) -> SFTRecord:
    """Join the actual saved request with its successful assistant event, not a checkpoint.

    The original artifacts stay in the audit store. This portable projection
    drops opaque input reasoning and exports only visible text/structured calls.
    Tokenization and alignment to a target model remain the caller's obligation.
    """
    if task.split != "train":
        raise ValueError("SFT export accepts only admitted train tasks")
    _event_hash(context_event)
    completion_hash = _event_hash(reply_event)
    if context_event.get("event") != "context_built" or reply_event.get("event") != "model_reply":
        raise ValueError("request must pair with a completed model_reply event")
    run_id, turn = reply_event.get("run_id"), reply_event.get("turn")
    if not isinstance(run_id, str) or type(turn) is not int or turn < 1:
        raise ValueError("invalid completion identity")
    payload = artifact.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("missing actual request payload")
    request_hash = digest(json.dumps(payload, sort_keys=True, ensure_ascii=False))
    if (
        context_event.get("run_id") != run_id
        or context_event.get("request_sha256") != request_hash
        or reply_event.get("request_sha256") != request_hash
        or context_event.get("policy_version") != artifact.get("policy_version")
        or context_event.get("request_artifact")
        != f"runs/{run_id}/requests/{turn:04d}-{request_hash}.json"
    ):
        raise ValueError("request/completion provenance mismatch")
    messages = deepcopy(payload.get("input" if protocol == "responses" else "messages"))
    if not isinstance(messages, list):
        raise ValueError("missing actual request messages")
    if protocol == "anthropic" and payload.get("system"):
        messages = [{"role": "system", "content": payload["system"]}, *messages]
    converted = canonicalize(
        messages, provider=provider, protocol=protocol, model=payload.get("model")
    )
    text, calls = reply_event.get("text"), reply_event.get("calls")
    if not isinstance(text, str) or not isinstance(calls, list):
        raise ValueError("invalid assistant target")
    target = [{"role": "assistant", "content": text}] if text else []
    seen = set()
    for raw in calls:
        call = ToolCall.model_validate(raw)
        if not call.call_id.strip() or call.call_id in seen:
            raise ValueError("duplicate or empty assistant tool id")
        seen.add(call.call_id)
        target.append(
            {
                "type": "function_call",
                "call_id": call.call_id,
                "name": call.name,
                "arguments": json.dumps(call.arguments, ensure_ascii=False, allow_nan=False),
            }
        )
    return SFTRecord(
        task=task,
        run_id=run_id,
        turn=turn,
        request_sha256=request_hash,
        completion_event_sha256=completion_hash,
        provider=provider,
        protocol=protocol,
        teacher_model=payload["model"],
        context_policy_version=artifact["policy_version"],
        prompt_messages=converted.messages,
        source_tools=deepcopy(payload.get("tools", [])),
        assistant_target=target,
        omitted_prompt_reasoning=converted.omitted_reasoning,
    )


class TokenizedDecision(Record):
    request_sha256: Hash
    rendered_sample_sha256: Hash
    input_ids: list[Token] = Field(min_length=2)
    prompt_length: int = Field(ge=1)
    assistant_mask: list[StrictBool]

    @model_validator(mode="after")
    def aligned(self):
        if self.prompt_length >= len(self.input_ids) or len(self.assistant_mask) != len(
            self.input_ids
        ):
            raise ValueError("token and mask lengths must match a nonempty completion")
        if any(self.assistant_mask[: self.prompt_length]) or not any(self.assistant_mask):
            raise ValueError("only the target assistant completion may be supervised")
        return self

    @property
    def labels(self) -> list[int]:
        return [
            token if learn else -100
            for token, learn in zip(self.input_ids, self.assistant_mask, strict=True)
        ]


class PolicyDecision(TokenizedDecision):
    behavior_logprobs: list[FiniteFloat]
    reference_logprobs: list[FiniteFloat]

    @model_validator(mode="after")
    def probabilities(self):
        length = len(self.input_ids) - self.prompt_length
        if any(
            len(values) != length for values in (self.behavior_logprobs, self.reference_logprobs)
        ):
            raise ValueError(
                "logprobs must align with every generated token, including masked tokens"
            )
        if any(value > 0 for value in self.behavior_logprobs + self.reference_logprobs):
            raise ValueError("log probabilities cannot be positive")
        return self


class RolloutVersions(Record):
    actor_sha256: Hash
    reference_sha256: Hash
    tokenizer_sha256: Hash
    chat_template_sha256: Hash
    harness_revision: Revision
    context_policy_version: Name
    tool_schema_sha256: Hash
    reward_version: Hash
    reasoning_mode: Name
    sampling_parameters: dict[str, Any]

    @model_validator(mode="after")
    def serializable_sampling(self):
        json.dumps(self.sampling_parameters, allow_nan=False)
        return self


class GRPORollout(Record):
    task: TaskRecord
    run_id: Name
    group_id: Name
    sample_index: int = Field(ge=0)
    seed: Annotated[StrictInt, Field(ge=0)]
    versions: RolloutVersions
    decisions: list[PolicyDecision] = Field(min_length=1)
    termination: Literal[
        "success",
        "candidate_failure",
        "budget_exhausted",
        "parse_error",
        "cancelled",
        "infrastructure_error",
        "verifier_error",
    ]
    reward: FiniteFloat | None
    independent_verifier: StrictBool
    max_steps: int = Field(ge=1)
    max_turns: int = Field(ge=1)
    max_generated_tokens: int = Field(ge=1)
    max_seconds: FiniteFloat = Field(gt=0)
    tool_steps: int = Field(ge=0)
    elapsed_seconds: FiniteFloat = Field(ge=0)

    @model_validator(mode="after")
    def eligible(self):
        if self.task.split != "train":
            raise ValueError("training rollout requires an admitted train task")
        if self.reward is not None and not self.independent_verifier:
            raise ValueError("a training reward requires an independent verifier")
        if (
            self.termination in {"cancelled", "infrastructure_error", "verifier_error"}
            and self.reward is not None
        ):
            raise ValueError("operational failures must remain ungraded")
        generated = sum(len(item.input_ids) - item.prompt_length for item in self.decisions)
        if self.tool_steps > len(self.decisions):
            raise ValueError("single-action decisions allow at most one tool step each")
        if (
            self.tool_steps > self.max_steps
            or len(self.decisions) > self.max_turns
            or generated > self.max_generated_tokens
        ):
            raise ValueError("rollout exceeds the frozen action/token budget")
        if self.elapsed_seconds > self.max_seconds and self.termination != "budget_exhausted":
            raise ValueError("wall-clock exhaustion needs an explicit budget termination")
        return self


def validate_group(rollouts: list[GRPORollout]) -> None:
    if len(rollouts) < 2 or any(item.reward is None for item in rollouts):
        raise ValueError("a GRPO update group requires at least two graded samples")
    first = rollouts[0]
    fields = (
        "task",
        "group_id",
        "versions",
        "max_steps",
        "max_turns",
        "max_generated_tokens",
        "max_seconds",
    )
    if any(
        any(getattr(item, field) != getattr(first, field) for field in fields) for item in rollouts
    ):
        raise ValueError("GRPO group must freeze task, versions, sampling and budgets")
    if len({item.sample_index for item in rollouts}) != len(rollouts) or len(
        {item.run_id for item in rollouts}
    ) != len(rollouts):
        raise ValueError("duplicate rollout identity in group")
