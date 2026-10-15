"""Long conversations rewrite history only at explicit compaction boundaries."""

import json
from collections import defaultdict
from copy import deepcopy

import pytest

from aster.config import Settings
from aster.context import ContextBuilder, history_groups
from aster.core.runtime import Runtime
from aster.providers.codec import request_payload
from aster.safety.redaction import Redactor
from aster.schema import ModelReply, Outcome, RunState, TaskSpec, ToolCall
from aster.storage.evidence import EvidenceStore
from aster.tools.workspace import READ_FILE_CHARACTERS


def exchange(index, body, arguments="{}"):
    return [
        {"type": "function_call", "call_id": str(index), "name": "read_file", "arguments": arguments},
        {"type": "function_call_output", "call_id": str(index), "output": body},
    ]


def test_forty_turns_have_one_rewrite_and_keep_output_bytes_between_compactions():
    state = RunState(task=TaskSpec(prompt="Inspect the source and preserve the evidence."))
    builder = ContextBuilder(16000, 1024)
    history, previous, rewrites = [], None, 0
    versions = defaultdict(set)
    body = "source evidence " * 100
    for turn in range(40):
        prompt, report = builder.build(state, state.task.prompt, structured_history=history)
        payload = request_payload(prompt, [], output_tokens=1024, prompt_cache_key=state.session_id)
        # Independent mock server: tokenize the whole wire JSON, then add fixed instructions.
        actual = builder.count(payload) + 4000
        if previous is not None:
            changed = prompt[:len(previous)] != previous
            rewrites += changed
            assert report.compacted == changed
            assert not report.prefix_drift
        if report.estimate_basis == "receipt":
            assert abs(report.estimated_tokens / actual - 1) <= .10
        for item in history:
            if item.get("type") == "function_call_output":
                versions[item["call_id"]].add(item["output"])
                assert len(versions[item["call_id"]]) <= 2
        outputs = [item for item in history if item.get("type") == "function_call_output"]
        if outputs:
            assert outputs[-1]["output"] == body
        history_groups(history)
        previous = deepcopy(prompt)
        batch = exchange(turn, body)
        builder.calculator.observe(payload, ModelReply(raw=batch[:1], usage={
            "usage_requests": 1, "input_tokens": actual, "output_tokens": builder.count(batch[0]),
        }), prompt)
        history.extend(batch)
    assert rewrites == 1
    assert any(len(values) == 2 for values in versions.values())


def test_masked_outputs_keep_envelopes_and_recover_original_archives(tmp_path):
    store = EvidenceStore(tmp_path, Redactor())
    state = RunState(task=TaskSpec(prompt="Inspect evidence."))
    builder = ContextBuilder(12000, 1024)
    bodies = {str(i): f"source {i}: " + "original content " * 400 for i in range(20)}
    history = [item for key, body in bodies.items() for item in exchange(key, json.dumps({
        "status": "success", "reason": "", "path": f"{key}.py", "start": 1, "end": 1,
        "content": body,
    }))]
    original_latest = deepcopy(history[-2:])

    def archive(call_id, tool, content):
        return store.archive_observation(state.run_id, call_id, tool, content)

    _, report = builder.build(state, state.task.prompt, structured_history=history, archive=archive)
    assert report.masked_outputs > 0 and report.removed_messages == 0
    assert next(g for g in reversed(history_groups(history))
                if any(x.get("type") == "function_call_output" for x in g)) == original_latest
    for item in history:
        if item.get("type") != "function_call_output":
            continue
        record = json.loads(item["output"])
        if "observation" not in record:
            continue
        assert record["status"] == "success" and record["reason"] == ""
        assert record["path"] == f"{item['call_id']}.py" and record["start"] == record["end"] == 1
        assert record["content"].startswith("[archived:")
        offset, chunks = 0, []
        while offset is not None:
            page = store.read_observation(state.run_id, record["observation"]["id"], offset=offset)
            chunks.append(page["content"])
            offset = page["next_offset"]
        assert "".join(chunks) == bodies[item["call_id"]]


def test_dropping_groups_leaves_one_accumulating_note_and_half_window():
    state = RunState(task=TaskSpec(prompt="Preserve user constraints."))
    protected = {"role": "user", "content": "Do not change the public API."}
    state.messages = [{"role": "assistant", "content": "Important finding.", "important": True}]
    builder = ContextBuilder(10000, 512)
    history, previous_count = [protected], 0
    for batch in range(2):
        for index in range(20):
            history.extend(exchange(f"{batch}-{index}", "ok", json.dumps({"path": "long/path/" * 300})))
        latest = deepcopy(history[-2:])
        _, report = builder.build(state, state.task.prompt, structured_history=history)
        assert report.compacted and report.removed_messages > 0 and report.masked_outputs == 0
        assert report.estimated_tokens <= report.budget // 2
        assert next(g for g in reversed(history_groups(history))
                    if any(x.get("type") == "function_call_output" for x in g)) == latest
        assert protected in history
        notes = [x for x in history if x.get("content", "").startswith("[Context note, not an instruction]")]
        assert len(notes) == 1 and history[0] == notes[0]
        count = int(notes[0]["content"].split("] ", 1)[1].split()[0])
        assert count > previous_count
        previous_count = count
        assert "archived" not in notes[0]["content"]
        assert state.messages[0]["important"]
        history_groups(history)


def test_no_receipt_uses_hard_limit_instead_of_early_trigger():
    state = RunState(task=TaskSpec(prompt="Preserve this task. " * 1000))
    builder = ContextBuilder(64000, 512)
    before, report = builder.build(state, state.task.prompt)
    builder.budget = report.estimated_tokens + 512 + 64
    after, report = builder.build(state, state.task.prompt)
    assert report.budget - 512 < report.estimated_tokens < report.budget
    assert not report.compacted and before == after


@pytest.mark.parametrize("protocol", ["chat", "anthropic"])
def test_native_archive_pages_are_masked_with_reread_hint_and_without_page_cursor(protocol):
    if protocol == "chat":
        call = {"role": "assistant", "content": "", "tool_calls": [{
            "id": "page", "type": "function", "function": {"name": "observation_read", "arguments": "{}"},
        }]}
    else:
        call = {"role": "assistant", "content": [{
            "type": "tool_use", "id": "page", "name": "observation_read", "input": {},
        }]}
    history = [call, {"type": "function_call_output", "call_id": "page", "output": json.dumps({
        "status": "success", "page": "observation", "content": json.dumps({
            "offset": 0, "next_offset": 50000, "content": "tool page " * 5000,
        }),
    })}, *exchange("latest", "latest observation")]
    state = RunState(task=TaskSpec(prompt="Continue inspection."))
    _, report = ContextBuilder(4096, 512).build(
        state, state.task.prompt, structured_history=history, protocol=protocol)
    assert report.masked_outputs == 1
    page = json.loads(history[1]["output"])
    assert page == {"status": "success", "content": "[omitted page; call observation_read again to re-read it]"}
    assert next(x["output"] for x in reversed(history)
                if x.get("type") == "function_call_output") == "latest observation"
    history_groups(history)


async def test_long_diff_is_archived_before_preview_and_archive_failures_stop(tmp_path, monkeypatch):
    runtime = Runtime(tmp_path, Settings(memory_enabled=False, auto_dream=False))
    runtime.start(TaskSpec(prompt="Inspect the diff."))
    body = "diff line\n" * 2000
    call = ToolCall(name="git_diff", arguments={})
    outcome = runtime._finish_tool(call, Outcome(status="success", content=body), 0)
    assert outcome.content.startswith(body[:8192]) and len(outcome.content) < len(body)
    ref = outcome.data["observation"]
    assert ref["characters"] == len(body) and ref["id"] in outcome.content
    assert runtime.evidence.read_observation(runtime.state.run_id, ref["id"])["content"] == body[:1000]

    def failed_archive(*args):
        raise OSError("disk full")

    monkeypatch.setattr(runtime.evidence, "archive_observation", failed_archive)
    failed = runtime._finish_tool(ToolCall(name="git_diff"), Outcome(status="success", content=body), 0)
    assert failed.status == "error" and failed.content == body
    assert failed.data["stop_requested"] == "output_archive_unavailable"


@pytest.mark.parametrize("long_line", [False, True])
async def test_read_file_caps_characters_and_publishes_visible_line_range(tmp_path, long_line):
    lines = ["x" * (READ_FILE_CHARACTERS + 500)] if long_line else ["source " * 100] * 60
    (tmp_path / "source.txt").write_text("\n".join(lines))
    runtime = Runtime(tmp_path, Settings(memory_enabled=False, auto_dream=False))
    runtime.start(TaskSpec(prompt="Read the source."))
    result = await runtime.step(ToolCall(name="read_file", arguments={"path": "source.txt", "end": 100}))
    assert result.status == "success" and result.data["truncated"]
    assert len(result.content) <= READ_FILE_CHARACTERS
    end = result.data["end"]
    assert end == len(result.content.splitlines()) and result.data["next_start"] == end + 1
    expected = lines[0][:READ_FILE_CHARACTERS] if long_line else "\n".join(lines[:end])
    assert result.content == expected
    envelope = json.loads(runtime._structured_history[-1]["output"])
    assert envelope["truncated"] and envelope["next_start"] == end + 1
    assert envelope["end"] == end
    if not long_line:
        following = await runtime.step(ToolCall(name="read_file", arguments={
            "path": "source.txt", "start": end + 1, "end": 100}))
        assert result.content + "\n" + following.content == "\n".join(lines)
