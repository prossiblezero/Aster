"""Receipt anchors survive appends, but never history/schema changes or bad receipts."""

from copy import deepcopy

import pytest

from aster.config import Settings
from aster.context import ContextBuilder
from aster.core.runtime import Runtime
from aster.providers.codec import request_payload
from aster.schema import ModelReply, RunState, TaskSpec


def anchored():
    state = RunState(task=TaskSpec(prompt="Inspect the code."))
    builder = ContextBuilder(16000, 512)
    prompt, _ = builder.build(state, state.task.prompt, model="test")
    payload = request_payload(prompt, [], model="test", output_tokens=512,
                              prompt_cache_key=state.session_id)
    reply = ModelReply(usage={"usage_requests": 1, "input_tokens": 5000, "output_tokens": 80})
    builder.calculator.observe(payload, reply, prompt)
    return state, builder, prompt, payload


def test_receipt_charges_only_new_observations_and_appended_context_notes():
    _, builder, prompt, payload = anchored()
    calculator = builder.calculator
    additions = [
        {"type": "reasoning", "encrypted_content": "opaque" * 100},
        {"type": "function_call", "call_id": "c", "name": "read_file", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "c", "output": "source code"},
        {"role": "assistant", "content": "Here is what I found."},
        {"role": "user", "content": "Also check the callers."},
    ]
    tail = {"role": "user", "content": "new workflow state " * 100}
    current = [*prompt, *additions, tail]
    expected = (5080 + calculator.count(additions[2]) + calculator.count(additions[4])
                + calculator.count(tail))
    assert calculator.estimate(current, {**payload, "input": current}) == (expected, "receipt")
    current[-1] = {"role": "user", "content": "short"}
    assert calculator.estimate(current, {**payload, "input": current}) == (
        5080 + calculator.count(additions[2]) + calculator.count(additions[4])
        + calculator.count(current[-1]), "receipt")


@pytest.mark.parametrize("change", ["replace_last", "remove_last"])
def test_receipt_anchor_covers_the_final_input_item(change):
    _, builder, prompt, payload = anchored()
    current = deepcopy(prompt)
    if change == "replace_last":
        current[-1]["content"] = "replaced state"
    else:
        current.pop()
    payload = {**payload, "input": current}
    assert builder.calculator._first_changed_item(current) == len(prompt) - 1
    assert builder.calculator.estimate(current, payload) == (
        builder.calculator.request_tokens(payload), "local")


def test_history_drift_is_located_before_falling_back_to_local_count():
    state, builder, prompt, payload = anchored()
    changed = deepcopy(prompt)
    changed[1]["content"] = "A different task"
    payload = {**payload, "input": changed}
    assert builder.calculator.estimate(changed, payload) == (
        builder.calculator.request_tokens(payload), "local")
    _, report = builder.build(state, "A different task", model="test")
    assert report.prefix_drift and report.first_changed_item == 1


@pytest.mark.parametrize("receipt", [
    ModelReply(),
    ModelReply(usage={"usage_requests": 2, "input_tokens": 20000, "output_tokens": 200}),
    ModelReply(diagnostics={"classification": "success", "flags": [],
                            "usage": {"usage_requests": 1, "input_tokens": True, "output_tokens": 10}}),
    ModelReply(usage={"usage_requests": 1, "input_tokens": 5000, "output_tokens": -1}),
    ModelReply(diagnostics={"classification": "transport", "usage": {}, "flags": []}),
    ModelReply(diagnostics={"classification": "success", "usage": {}, "flags": ["mismatch"]}),
])
def test_bad_receipt_clears_previous_anchor(receipt):
    _, builder, prompt, payload = anchored()
    builder.calculator.observe(payload, receipt, prompt)
    assert builder.calculator.estimate(prompt, payload)[1] == "local"


def test_loading_a_tool_does_not_reuse_a_smaller_schema_receipt():
    _, builder, prompt, payload = anchored()
    payload = {**payload, "tools": [{"name": "new_tool", "description": "long schema " * 500}]}
    assert builder.calculator.estimate(prompt, payload) == (
        builder.calculator.request_tokens(payload), "local")


async def test_continuation_keeps_anchor_but_new_session_or_model_clears_it(tmp_path):
    class ReceiptModel:
        async def complete(self, messages, tools):
            return ModelReply(text="Inspected.", usage={
                "usage_requests": 1, "input_tokens": 5000, "output_tokens": 80})

    runtime = Runtime(tmp_path, Settings(completion="off", memory_enabled=False), model=ReceiptModel())
    await runtime.run(TaskSpec(prompt="Inspect the code."))
    calculator = runtime.context.calculator
    runtime.continue_task("Inspect the callers too.")
    _, _, report = runtime.prepare_request()
    assert report.estimate_basis == "receipt" and not report.prefix_drift
    runtime.settings.model = "other"
    assert runtime.prepare_request()[2].estimate_basis == "local"
    assert calculator._anchor is None
    await runtime.run()
    runtime.start(TaskSpec(prompt="A separate task."))
    assert runtime.prepare_request()[2].estimate_basis == "local"
    assert calculator._anchor is None
