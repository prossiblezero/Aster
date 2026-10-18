"""Token-in, token-out rollouts: exact ids, robust parsing, labels that keep failures honest."""

import json

import httpx
import pytest

from aster.schema import VerifierResult
from aster.training.env import AsterEnv
from aster.training.qwen import parse_completion
from aster.training.rollout import Generation, VLLMCompletions, rollout, training_settings

pytestmark = pytest.mark.core

TOOLS = [{"type": "function", "function": {"name": "read_file", "parameters": {"properties": {
    "path": {"type": "string"}, "start": {"type": "integer"}, "end": {"type": "integer"}}}}}]


def test_hermes_and_xml_tool_calls_parse_with_schema_types():
    hermes = parse_completion('Let me look.\n<tool_call>\n{"name": "read_file", "arguments": {"path": "a.py"}}\n</tool_call>')
    assert hermes.text == "Let me look." and hermes.calls == [("read_file", {"path": "a.py"})]
    xml = parse_completion("<think>plan</think>\n<tool_call>\n<function=read_file>\n<parameter=path>\na.py\n"
                           "</parameter>\n<parameter=start>\n3\n</parameter>\n</function>\n</tool_call>", TOOLS)
    assert xml.calls == [("read_file", {"path": "a.py", "start": 3})] and xml.reasoning == "plan"
    assert parse_completion("plan</think>Done.").text == "Done."  # template opened <think>


@pytest.mark.parametrize("text,error", [
    ('<tool_call>{"name": "read_file", "arguments": {"path": ', "not closed"),
    ('<tool_call>{"name": read_file}</tool_call>', "not valid JSON"),
    ("<tool_call><function=read_file><parameter=start>x</parameter></function></tool_call>", "malformed parameter"),
    ("<think>still thinking", "cut off"),
])
def test_malformed_completions_are_reported_not_guessed(text, error):
    parsed = parse_completion(text, TOOLS)
    assert not parsed.calls and any(error in message for message in parsed.errors)


class CharTokenizer:
    """Stand-in for a chat template: one token per character of a JSON rendering."""

    def apply_chat_template(self, conversation, *, tools=None, add_generation_prompt=False, tokenize=True, **_):
        text = json.dumps({"messages": conversation, "tools": [t["function"]["name"] for t in tools]}) + "<|assistant|>"
        return [ord(c) for c in text]

    def decode(self, token_ids, **_):
        return "".join(map(chr, token_ids))


def scripted(*completions):
    queue = list(completions)

    async def generate(prompt_ids):
        if not queue:
            raise RuntimeError("generation server unavailable")
        text = queue.pop(0)
        return Generation([ord(c) for c in text], [-0.1] * len(text), "stop")
    return generate


def env_for(tmp_path):
    (tmp_path / "app.py").write_text("VALUE = 0\n")
    settings = training_settings(model="qwen-test", base_url="http://127.0.0.1:9/v1")

    def verifier(root, state):
        return VerifierResult(passed=(root / "app.py").read_text() == "VALUE = 1\n", reason="hidden tests")
    return AsterEnv(tmp_path, settings, verifier=verifier)


@pytest.mark.asyncio
async def test_episode_records_exact_tokens_and_survives_a_malformed_turn(tmp_path):
    generate = scripted(
        "<tool_call>\n<function=read_file>\n<parameter=path>\napp.py\n</parameter>\n</function>\n</tool_call>",
        '<tool_call>{"name": "patch_file", "arguments": {"path": "app.py", "old_text": ',  # cut off
        'Fixing.<tool_call>{"name": "patch_file", "arguments": {"path": "app.py", '
        '"old_text": "VALUE = 0", "new_text": "VALUE = 1"}}</tool_call>',
        "VALUE is now 1.",
    )
    trajectory = await rollout(env_for(tmp_path), "Set VALUE to 1 in app.py", CharTokenizer(), generate)
    assert [bool(t.parse_errors) for t in trajectory.turns] == [False, True, False, False]
    assert trajectory.label == "success" and trajectory.reward == 1.0 and trajectory.trainable
    for turn in trajectory.turns:
        assert len(turn.logprobs) == len(turn.completion_ids)
        assert sum(turn.loss_mask) == len(turn.completion_ids)
    # The malformed reply and its error are what the policy sees next.
    assert "could not be executed" in CharTokenizer().decode(trajectory.turns[2].prompt_ids)


@pytest.mark.asyncio
async def test_infrastructure_failure_is_ungraded_and_not_trainable(tmp_path):
    trajectory = await rollout(env_for(tmp_path), "Set VALUE to 1", CharTokenizer(), scripted())
    assert trajectory.label == "infrastructure_error" and trajectory.reward is None
    assert not trajectory.trainable and "generation server unavailable" in trajectory.info["error"]


@pytest.mark.asyncio
async def test_training_settings_drop_workflow_notes(tmp_path):
    env = env_for(tmp_path)
    observation, _ = env.reset("Set VALUE to 1")
    assert not any("Context update" in str(item.get("content", "")) for item in observation["model_input"])
    env.close()


@pytest.mark.asyncio
async def test_vllm_completions_reads_token_ids_back_exactly():
    def handler(request):
        body = json.loads(request.content)
        assert body["prompt"] == [1, 2, 3] and body["return_tokens_as_token_ids"] is True
        return httpx.Response(200, json={"choices": [{"finish_reason": "length", "logprobs": {
            "tokens": ["token_id:42", "token_id:7"], "token_logprobs": [-0.5, -1.25]}}]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    generation = await VLLMCompletions("http://vllm/v1", "qwen", client=client)([1, 2, 3])
    assert (generation.token_ids, generation.logprobs, generation.finish_reason) == ([42, 7], [-0.5, -1.25], "length")
