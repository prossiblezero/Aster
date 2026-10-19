"""State updates preserve the complete request prefix until explicit compaction."""

import json
from copy import deepcopy

import pytest

from aster.config import Settings
from aster.context import ContextBuilder, history_groups
from aster.context.prompt import (
    CONTEXT_NOTE_PREFIX,
    CONTEXT_SECTIONS,
    context_notes,
    is_context_note,
    workflow_state,
)
from aster.core.runtime import Runtime
from aster.providers.codec import request_payload
from aster.safety.redaction import Redactor
from aster.schema import ModelReply, RunState, TaskSpec, Verification

pytestmark = pytest.mark.core


def exchange(index, body):
    return [
        {"type": "function_call", "call_id": str(index), "name": "read_file", "arguments": "{}"},
        {"type": "function_call_output", "call_id": str(index), "output": body},
    ]


def test_thirty_turns_append_only_changed_state_and_preserve_full_input():
    state = RunState(task=TaskSpec(prompt="Inspect the code.", max_turns=500, max_steps=500))
    state.working_memory = {"old.py": {"summary": "DUPLICATE_WORKING_MEMORY"}}
    builder = ContextBuilder(64000, 8192)
    history, previous = [], []
    for turn in range(30):
        state.turn = state.step = turn
        changed = turn == 0
        if turn and turn % 3 == 0:
            state.verification = Verification(state="passed", command=f"test_{turn}")
            changed = True
        if turn and turn % 5 == 0:
            state.changed_paths.append(f"source_{turn}.py")
            changed = True
        prompt, report = builder.build(state, state.task.prompt, structured_history=history)
        assert prompt[:len(previous)] == previous
        assert not report.compacted and not report.prefix_drift
        assert report.context_notes == int(changed)
        assert report.sections["history"] == builder.count(prompt[2:])
        for forbidden in ("Context sections", "working_memory", "remaining_turns", "DUPLICATE_WORKING_MEMORY"):
            assert forbidden not in json.dumps(prompt)
        assert history == prompt[2:]
        payload = request_payload(prompt, [], output_tokens=8192, prompt_cache_key=state.session_id)
        assert payload["input"] == prompt
        previous = deepcopy(prompt)
        batch = exchange(turn, "source evidence " * 100)
        builder.calculator.observe(payload, ModelReply(raw=batch[:1], usage={
            "usage_requests": 1, "input_tokens": builder.count(payload) + 4000,
            "output_tokens": builder.count(batch[0]),
        }), prompt)
        history.extend(batch)
        history_groups(history)


def test_budget_updates_only_at_low_and_critical_thresholds():
    state = RunState(task=TaskSpec(prompt="Finish the task.", max_turns=40, max_steps=40))
    builder, history = ContextBuilder(64000, 8192), []
    for turn in range(41):
        state.turn = turn
        _, report = builder.build(state, state.task.prompt, structured_history=history)
        budget = workflow_state(state).get("budget")
        if turn < 30:
            assert budget is None
            assert report.context_notes == int(turn == 0)
        elif turn < 36:
            assert budget == {"level": "low", "remaining_fraction_at_most": .25}
            assert report.context_notes == int(turn == 30)
        else:
            assert budget == {"level": "critical", "remaining_turns": 40 - turn, "remaining_steps": 40}
            assert report.context_notes == 1


@pytest.mark.parametrize("axis", ["step", "tokens"])
def test_budget_uses_the_tightest_limit_and_clamps_exhaustion(axis):
    state = RunState(task=TaskSpec(prompt="Finish.", max_tokens=1000))
    for used, level in ((0, None), (75, "low"), (90, "critical"), (120, "critical")):
        if axis == "step":
            state.step = used * 40 // 100
        else:
            state.team_usage["charged_tokens"] = used * 10
        budget = workflow_state(state).get("budget", {})
        assert budget.get("level") == level
        if level == "critical":
            assert budget["remaining_steps"] == max(0, 40 - state.step)
            assert budget["remaining_team_tokens"] == max(0, 1000 - state.team_usage.get("charged_tokens", 0))
    state = RunState(task=TaskSpec(prompt="Small budget.", max_turns=3))
    assert workflow_state(state)["budget"]["level"] == "critical"


def test_compaction_replaces_old_notes_with_full_state_then_resumes_appends():
    state = RunState(task=TaskSpec(prompt="Inspect evidence.", max_turns=500, max_steps=500))
    state.active_skills = ["review"]
    builder, history = ContextBuilder(12000, 1024), []
    for index in range(20):
        history.extend(context_notes(history, {"memory": f"memory {index}", "skills": f"catalog {index}"}))
        history.extend(exchange(index, "original content " * 400))
    latest = deepcopy(history[-2:])
    kwargs = dict(memory="current memory", skills="current catalog", skill_bodies={"review": "current body"})
    prompt, report = builder.build(state, state.task.prompt, structured_history=history, **kwargs)
    assert report.compacted and report.masked_outputs > 0
    notes = [g[0] for g in history_groups(history) if len(g) == 1 and is_context_note(g[0])]
    assert report.context_notes == len(notes) == len(CONTEXT_SECTIONS)
    assert history[-4:] == notes
    assert history[-6:-4] == latest
    for name, note in zip(CONTEXT_SECTIONS, notes, strict=True):
        assert note["content"].startswith(CONTEXT_NOTE_PREFIX + name + ":\n")
    payload = request_payload(prompt, [], output_tokens=1024, prompt_cache_key=state.session_id)
    batch = exchange("next", "next observation")
    builder.calculator.observe(payload, ModelReply(usage={
        "usage_requests": 1, "input_tokens": builder.count(payload), "output_tokens": builder.count(batch[0]),
    }), prompt)
    history.extend(batch)
    after, report = builder.build(state, state.task.prompt, structured_history=history, **kwargs)
    assert after[:len(prompt)] == prompt
    assert not report.compacted and not report.prefix_drift and report.context_notes == 0


def test_effective_value_is_derived_from_history_including_clears_and_branches():
    history = context_notes([], {"memory": "first", "skills": "catalog"})
    original = deepcopy(history)
    assert context_notes(history, {"memory": "first", "skills": "catalog"}) == []
    cleared = context_notes(history, {"skills": "catalog"})
    assert cleared == [{"role": "user", "content": CONTEXT_NOTE_PREFIX + "memory:\n[cleared]"}]
    history.extend(cleared)
    assert context_notes(history, {"skills": "catalog"}) == []
    assert context_notes(history, {"memory": "first", "skills": "catalog"}) == original[:1]
    assert context_notes(original, {"memory": "first", "skills": "catalog"}) == []
    assert not is_context_note({"role": "assistant", "content": cleared[0]["content"]})
    assert not is_context_note({"role": "user", "content": [{"type": "text", "text": "note"}]})


@pytest.mark.parametrize("protocol", ["responses", "chat", "anthropic"])
def test_protocol_keeps_tool_results_before_context_text(protocol):
    notes = context_notes([], {"memory": "source facts", "skills": "review catalog"})
    assert len(notes) == 2
    history = [*exchange("c1", "source text"), *notes]
    payload = request_payload(history, [], protocol=protocol)
    wire = payload.get("input", payload.get("messages"))
    history_groups(wire)
    if protocol == "anthropic":
        assert [block["type"] for block in wire[-1]["content"]] == ["tool_result", "text", "text"]
        assert [block["text"] for block in wire[-1]["content"][1:]] == [n["content"] for n in notes]
    else:
        assert wire[-2:] == notes


def test_resume_preserves_entire_last_request_prefix(tmp_path):
    settings = Settings(memory_enabled=False, auto_dream=False, context_tokens=64000, output_tokens=8192)
    runtime = Runtime(tmp_path, settings)
    runtime.start(TaskSpec(prompt="Inspect the source.", max_turns=500, max_steps=500))
    first, _, _ = runtime.prepare_request()
    runtime._structured_history.extend(exchange("c1", "source evidence"))
    runtime.state.step = runtime.state.turn = 1
    before, tools, _ = runtime.prepare_request()
    assert before[:len(first)] == first
    runtime.checkpoint()
    restored = Runtime(tmp_path, settings)
    restored.resume(runtime.state.run_id)
    restored._structured_history.extend(exchange("c2", "new evidence"))
    restored.state.changed_paths = ["source.py"]
    after, next_tools, report = restored.prepare_request()
    assert next_tools == tools and not report.compacted
    assert after[:len(before)] == before
    assert report.context_notes == 1
    assert restored.evidence.events(restored.state.run_id)[-1]["context_notes"] == 1


def test_notes_persist_only_redacted_values():
    state = RunState(task=TaskSpec(prompt="Inspect.", max_turns=500, max_steps=500))
    builder, history = ContextBuilder(64000, 8192), []
    redactor = Redactor(["secret-value"])
    prompt, report = builder.build(
        state, state.task.prompt, memory="private secret-value", structured_history=history,
        redact_history=lambda items: redactor.value(items, mask_fields=False),
    )
    assert report.context_notes == 2
    assert "secret-value" not in json.dumps(history)
    assert history == prompt[2:]
    again, report = builder.build(
        state, state.task.prompt, memory="private secret-value", structured_history=history,
        redact_history=lambda items: redactor.value(items, mask_fields=False),
    )
    assert again == prompt and report.context_notes == 0
    assert report.sections["context_notes"] == 0
