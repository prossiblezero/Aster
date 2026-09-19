"""History and wire requests must contain complete, uniquely identified tool batches."""

import json
from copy import deepcopy

import pytest

from aster.config import Settings
from aster.provider import ContextHistoryError, ModelClient, history_groups, request_payload
from aster.runtime import Runtime
from aster.schema import ModelReply, TaskSpec, ToolCall


def batch(protocol, calls=("a", "b"), outputs=("b", "a")):
    if protocol == "responses":
        return [
            {"type": "function_call", "call_id": key, "name": "read_file", "arguments": "{}"}
            for key in calls
        ] + [
            {"type": "function_call_output", "call_id": key, "output": "observed"}
            for key in outputs
        ]
    if protocol == "chat":
        return [
            {
                "role": "assistant",
                "content": None,
                "reasoning_content": "opaque reasoning",
                "tool_calls": [
                    {
                        "id": key,
                        "type": "function",
                        "function": {"name": "read_file", "arguments": "{}"},
                    }
                    for key in calls
                ],
            }
        ] + [{"role": "tool", "tool_call_id": key, "content": "observed"} for key in outputs]
    return [
        {
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "opaque", "signature": "unchanged"},
                *[
                    {"type": "tool_use", "id": key, "name": "read_file", "input": {}}
                    for key in calls
                ],
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": key, "content": "observed"}
                for key in outputs
            ],
        },
    ]


@pytest.mark.parametrize("protocol", ["responses", "chat", "anthropic"])
def test_native_parallel_results_can_arrive_out_of_order(protocol):
    history = batch(protocol)
    original = deepcopy(history)
    assert history_groups(history) == [history]
    payload = request_payload(history, [], protocol=protocol)
    wire = payload.get("input", payload.get("messages"))
    assert history_groups(wire) == [wire]
    assert history == original


@pytest.mark.parametrize("protocol", ["responses", "chat", "anthropic"])
@pytest.mark.parametrize(
    "calls, outputs, error",
    [
        (("a", "a"), ("a",), "duplicate_tool_call"),
        (("a", "b"), ("b", "b"), "duplicate_tool_output"),
        (("a", "b"), ("a",), "missing_tool_output"),
        (("a",), ("unknown",), "orphan_tool_output"),
        (("",), ("",), "invalid_tool_id"),
        ((" ",), (" ",), "invalid_tool_id"),
        ((None,), (None,), "invalid_tool_id"),
        (("a",), ("",), "invalid_tool_id"),
    ],
)
def test_invalid_pairing_is_rejected_before_payload_conversion(protocol, calls, outputs, error):
    with pytest.raises(ContextHistoryError, match=error):
        request_payload(batch(protocol, calls, outputs), [], protocol=protocol)


@pytest.mark.parametrize("protocol", ["responses", "chat", "anthropic"])
@pytest.mark.parametrize(
    "interruption",
    [
        {"role": "assistant", "content": "Already finished"},
        {"role": "user", "content": "Next request"},
        {"type": "reasoning", "id": "new-turn", "summary": []},
    ],
)
def test_next_turn_cannot_interrupt_pending_results(protocol, interruption):
    history = batch(protocol, ("a",), ("a",))
    history.insert(1, interruption)
    with pytest.raises(ContextHistoryError, match="missing_tool_output"):
        request_payload(history, [], protocol=protocol)


@pytest.mark.parametrize("protocol", ["responses", "chat", "anthropic"])
def test_output_before_call_and_reused_ids_are_rejected(protocol):
    history = batch(protocol, ("a",), ("a",))
    with pytest.raises(ContextHistoryError, match="orphan_tool_output"):
        history_groups(history[::-1])
    with pytest.raises(ContextHistoryError, match="duplicate_tool_call"):
        history_groups(history + deepcopy(history))


@pytest.mark.parametrize("status", ["success", "error", "denied", "cancelled"])
@pytest.mark.parametrize("protocol", ["responses", "chat", "anthropic"])
def test_normalized_results_keep_pairing_regardless_of_execution_status(protocol, status):
    history = batch("responses")
    reasoning = {"type": "reasoning", "id": "reason", "encrypted_content": "opaque"}
    history.insert(0, reasoning)
    for item in history[-2:]:
        item["output"] = '{"status": "' + status + '", "content": "actual outcome"}'
    assert history_groups(history) == [history]
    payload = request_payload(history, [], protocol=protocol)
    wire = payload.get("input", payload.get("messages"))
    assert history_groups(wire) == [wire]
    if protocol == "responses":
        assert wire[0] == reasoning


def test_reasoning_prefix_and_following_tools_stay_together():
    history = [
        {"type": "reasoning", "id": "r1", "encrypted_content": "opaque1"},
        {"type": "reasoning", "id": "r2", "encrypted_content": "opaque2"},
        {"role": "assistant", "content": "Inspecting"},
        *batch("responses"),
    ]
    assert history_groups(history) == [history]


def test_new_calls_cannot_join_a_batch_after_results_started():
    first, second = batch("responses", ("a", "b"), ("a", "b")), batch("responses", ("c",), ("c",))
    history = first[:3] + second[:1] + first[3:] + second[1:]
    with pytest.raises(ContextHistoryError, match="missing_tool_output"):
        history_groups(history)


def test_anthropic_result_must_precede_user_text_and_complete_batch():
    history = batch("anthropic")
    history[-1]["content"].insert(0, {"type": "text", "text": "next request"})
    with pytest.raises(ContextHistoryError, match="invalid_tool_order"):
        history_groups(history)


@pytest.mark.parametrize("protocol", ["chat", "anthropic"])
def test_wire_conversion_cannot_send_an_incomplete_batch(monkeypatch, protocol):
    broken = batch(protocol, ("a",), ())[:1]
    monkeypatch.setattr(
        f"aster.providers.codec._{protocol}_messages",
        lambda messages: ("", broken) if protocol == "anthropic" else broken,
    )
    with pytest.raises(ContextHistoryError, match="missing_tool_output"):
        request_payload(batch("responses"), [], protocol=protocol)


@pytest.mark.parametrize("protocol", ["responses", "chat", "anthropic"])
async def test_model_client_rejects_incomplete_history_without_network(monkeypatch, protocol):
    async def unexpected_post(*args):
        pytest.fail("invalid history reached the network")

    monkeypatch.setattr(ModelClient, "_post", unexpected_post)
    with pytest.raises(ContextHistoryError, match="missing_tool_output"):
        await ModelClient(Settings(protocol=protocol)).complete(batch("responses", ("a",), ()), [])


async def test_duplicate_direct_step_preserves_resumable_transcript(tmp_path):
    settings = Settings(approval="auto", memory_enabled=False)
    runtime = Runtime(tmp_path, settings)
    runtime.start(TaskSpec(prompt="Inspect the repository"))
    call = ToolCall(name="list_files", call_id="one-call")
    assert (await runtime.step(call)).status == "success"
    original = deepcopy(runtime._structured_history)
    assert (await runtime.step(call)).reason == "duplicate_call_id"
    assert runtime._structured_history == original and runtime.state.step == 1
    checkpoint = json.loads(
        runtime.evidence.path("runs", runtime.state.run_id, "checkpoint.json").read_text()
    )
    assert history_groups(checkpoint["structured_history"]) == [original]

    restored = Runtime(tmp_path, settings)
    restored.resume(runtime.state.run_id)
    assert (await restored.step(call)).reason == "duplicate_call_id"
    assert restored._structured_history == original
    assert (await restored.step(ToolCall(name="list_files"))).status == "success"
    groups = history_groups(restored._structured_history)
    assert sum(any(item.get("type") == "function_call" for item in group) for group in groups) == 2


def test_checkpoint_and_resume_reject_incomplete_transcript_without_replacing_state(tmp_path):
    settings = Settings(memory_enabled=False)
    runtime = Runtime(tmp_path, settings)
    runtime.start(TaskSpec(prompt="Inspect the repository"))
    path = runtime.evidence.path("runs", runtime.state.run_id, "checkpoint.json")
    original = path.read_bytes()
    runtime._structured_history = batch("responses", ("unfinished",), ())
    with pytest.raises(ContextHistoryError, match="missing_tool_output"):
        runtime.checkpoint()
    assert path.read_bytes() == original

    record = json.loads(original)
    record["structured_history"] = runtime._structured_history
    path.write_text(json.dumps(record))
    restored = Runtime(tmp_path, settings)
    with pytest.raises(ContextHistoryError, match="missing_tool_output"):
        restored.resume(runtime.state.run_id)
    assert restored.state is None and restored._structured_history == []


@pytest.mark.parametrize("previous_call", [False, True])
async def test_repeated_model_call_ids_are_rejected_before_effects(tmp_path, previous_call):
    calls = [
        ToolCall(
            name="write_file", call_id="repeated", arguments={"path": "new.txt", "content": "x"}
        )
    ]
    if not previous_call:
        calls.append(calls[0].model_copy(deep=True))

    class DuplicateModel:
        async def complete(self, *_):
            return ModelReply(calls=calls)

    runtime = Runtime(
        tmp_path, Settings(approval="auto", memory_enabled=False), model=DuplicateModel()
    )
    runtime.start(TaskSpec(prompt="Inspect the repository"))
    if previous_call:
        await runtime.step(ToolCall(name="list_files", call_id="repeated"))
    runtime.prepare_request()
    original = deepcopy(runtime._structured_history)
    state = await runtime.turn()
    assert state.phase == "failed" and state.stop_reason == "context_history_invalid"
    assert not (tmp_path / "new.txt").exists()
    assert runtime._structured_history == original
    history_groups(original)


@pytest.mark.parametrize("source", ["direct", "model"])
async def test_blank_call_id_is_rejected_before_write_or_checkpoint(source, tmp_path):
    call = ToolCall(name="write_file", call_id=" \t", arguments={"path": "new.txt", "content": "x"})

    class BlankIdModel:
        async def complete(self, *_):
            return ModelReply(calls=[call])

    runtime = Runtime(tmp_path, Settings(approval="auto"), model=BlankIdModel())
    runtime.start(TaskSpec(prompt="Inspect the repository"))
    if source == "direct":
        assert (await runtime.step(call)).reason == "invalid_tool_id"
    else:
        assert (await runtime.turn()).stop_reason == "context_history_invalid"
    assert not (tmp_path / "new.txt").exists()
    assert runtime._structured_history == []
    runtime.checkpoint()
