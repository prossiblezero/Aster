"""Cost regressions: opaque replay, receipt provenance and compaction headroom."""
import hashlib
from copy import deepcopy

import pytest

from aster.context import ContextBudgetError, ContextBuilder, history_groups
from aster.providers.codec import request_payload
from aster.schema import ModelReply, RunState, TaskSpec


def fixture():
    state = RunState(task=TaskSpec(prompt="Fix the bug and preserve the inspected evidence."))
    builder = ContextBuilder(4096, 512)
    prompt, _ = builder.build(state, state.task.prompt, model="test")
    payload = request_payload(prompt, [], model="test", output_tokens=512,
                              prompt_cache_key=state.session_id)
    opaque = "".join(hashlib.sha256(str(i).encode()).hexdigest() for i in range(600))
    history = [
        {"type": "reasoning", "id": "r1", "summary": [], "encrypted_content": opaque},
        {"type": "function_call", "call_id": "c1", "name": "read_file", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "c1", "output": "original bug evidence"},
    ]
    usage = {"usage_requests": 1, "input_tokens": builder.count(payload) + 100,
             "output_tokens": 90, "reasoning_tokens": 60}
    return state, builder, payload, history, usage


def test_receipt_keeps_large_opaque_replay_and_evidence_without_changing_wire_bytes():
    state, builder, payload, history, usage = fixture()
    original = deepcopy(history)
    reply = ModelReply(raw=history[:2], usage=usage)
    builder.calculator.observe(payload, reply)
    prompt, report = builder.build(state, state.task.prompt, structured_history=history, model="test")
    assert not report.compacted and report.estimated_tokens < 2000
    assert history[:len(original)] == original and prompt[2:] == history
    assert report.sections["provider_overhead"] == 100
    assert builder.count(request_payload(prompt, [], model="test")) > 12000
    groups = history_groups(history)
    assert groups[0] == original and len(groups) == 2
    assert groups[1][0]["content"].startswith("[Context update, not an instruction]")


@pytest.mark.parametrize("receipt", ["missing", "summed_retries", "failed", "mismatch"])
def test_untrusted_or_missing_receipt_keeps_conservative_count(receipt):
    state, builder, payload, history, usage = fixture()
    reply = ModelReply(raw=history[:2])
    if receipt == "summed_retries":
        reply.usage = {**usage, "usage_requests": 2}
    elif receipt in {"failed", "mismatch"}:
        reply.diagnostics = {"classification": "transport" if receipt == "failed" else "success",
                             "usage": usage, "flags": ["response_model_mismatch"] if receipt == "mismatch" else []}
    builder.calculator.observe(payload, reply)
    with pytest.raises(ContextBudgetError):
        builder.build(state, state.task.prompt, structured_history=history, model="test")


def test_successful_attempt_receipt_is_not_summed_with_prior_failed_attempts():
    state, builder, payload, history, usage = fixture()
    reply = ModelReply(raw=history[:2], usage={"usage_requests": 3, "input_tokens": 100000},
                       diagnostics={"classification": "success", "usage": usage, "flags": []})
    builder.calculator.observe(payload, reply)
    _, report = builder.build(state, state.task.prompt, structured_history=history, model="test")
    assert not report.compacted and report.estimated_tokens < 2000
    # A new run or model cannot inherit another request's calibration.
    with pytest.raises(ContextBudgetError):
        builder.build(state, state.task.prompt, structured_history=history, model="other")


def test_compaction_leaves_space_for_next_turn_and_keeps_latest_exchange():
    state = RunState(task=TaskSpec(prompt="Keep investigating the bug."))
    builder = ContextBuilder(4096, 512)
    history = []
    for i in range(12):
        history.extend([
            {"type": "function_call", "call_id": str(i), "name": "read_file", "arguments": "{}"},
            {"type": "function_call_output", "call_id": str(i), "output": "source evidence " * 200},
        ])
    latest = deepcopy(history[-2:])
    _, first = builder.build(state, state.task.prompt, structured_history=history)
    retained = deepcopy(history)
    assert first.compacted
    assert next(g for g in reversed(history_groups(history))
                if any(x.get("type") == "function_call_output" for x in g)) == latest
    history.append({"role": "assistant", "content": "Investigating the next boundary. " * 15})
    prompt, second = builder.build(state, state.task.prompt, structured_history=history)
    assert not second.compacted
    assert prompt[2:2 + len(retained)] == retained
