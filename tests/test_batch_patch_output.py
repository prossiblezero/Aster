import json

import pytest

from aster.config import Settings
from aster.core.runtime import Runtime
from aster.schema import TaskSpec, ToolCall


async def editor(root, content):
    (root / "a.txt").write_bytes(content)
    runtime = Runtime(root, Settings(approval="auto", memory_enabled=False, max_workers=0))
    runtime.start(TaskSpec(prompt="edit"))
    await runtime.step(ToolCall(name="read_file", arguments={"path": "a.txt"}))
    return runtime


async def test_batch_matches_original_and_preserves_bom_and_mixed_newlines(tmp_path):
    runtime = await editor(tmp_path, b"\xef\xbb\xbfalpha\r\nbeta\ngamma\r\n")
    outcome = await runtime.step(ToolCall(name="patch_file", arguments={
        "path": "a.txt", "edits": [
            {"old_text": "gamma", "new_text": "third\nlast"},
            {"old_text": "alpha", "new_text": "beta"},
            {"old_text": "beta", "new_text": "second"},
        ],
    }))
    assert outcome.status == "success", outcome
    assert (tmp_path / "a.txt").read_bytes() == b"\xef\xbb\xbfbeta\r\nsecond\nthird\r\nlast\r\n"
    assert "a.txt" in runtime.state.read_evidence
    again = await runtime.step(ToolCall(name="patch_file", arguments={
        "path": "a.txt", "edits": [{"old_text": "beta", "new_text": "no"}],
    }))
    assert again.status == "success"
    assert (tmp_path / "a.txt").read_bytes() == b"\xef\xbb\xbfno\r\nsecond\nthird\r\nlast\r\n"


@pytest.mark.parametrize("edits,reason", [
    ([{"old_text": "alpha", "new_text": "x"}, {"old_text": "missing", "new_text": "y"}], "patch_nonunique"),
    ([{"old_text": "alpha", "new_text": "x"}, {"old_text": "alp", "new_text": "y"}], "patch_overlap"),
    ([{"old_text": "aa", "new_text": "x"}], "patch_nonunique"),
    ([], "invalid_arguments"),
])
async def test_invalid_batch_does_not_publish_any_edits(tmp_path, edits, reason):
    original = b"alpha\naaa\n"
    runtime = await editor(tmp_path, original)
    outcome = await runtime.step(ToolCall(name="patch_file", arguments={"path": "a.txt", "edits": edits}))
    assert outcome.reason == reason
    assert not outcome.changed_paths
    assert (tmp_path / "a.txt").read_bytes() == original


async def test_batch_approval_version_check_and_scope(tmp_path):
    runtime = await editor(tmp_path, b"alpha\nbeta\n")
    runtime.settings.approval = "ask"

    def approve(*_):
        (tmp_path / "a.txt").write_text("external\n")
        return True

    runtime.approval_callback = approve
    result = await runtime.step(ToolCall(name="patch_file", arguments={
        "path": "a.txt", "edits": [{"old_text": "alpha", "new_text": "new"}],
    }))
    assert result.reason == "fresh_read_required"
    assert (tmp_path / "a.txt").read_text() == "external\n"
    await runtime.step(ToolCall(name="read_file", arguments={"path": "a.txt"}))
    runtime.settings.write_scope = ["other.txt"]
    result = await runtime.step(ToolCall(name="patch_file", arguments={
        "path": "a.txt", "edits": [{"old_text": "external", "new_text": "new"}],
    }))
    assert result.reason == "write_scope"


async def test_patch_accepts_bom_from_actual_read(tmp_path):
    runtime = await editor(tmp_path, b"\xef\xbb\xbfalpha\r\nalpha\r\n")
    read = await runtime.step(ToolCall(name="read_file", arguments={"path": "a.txt", "end": 1}))
    result = await runtime.step(ToolCall(name="patch_file", arguments={
        "path": "a.txt", "edits": [{"old_text": read.content, "new_text": "beta"}],
    }))
    assert result.status == "success"
    assert (tmp_path / "a.txt").read_bytes() == b"\xef\xbb\xbfbeta\r\nalpha\r\n"


@pytest.mark.parametrize("tool", ["write_file", "patch_file"])
async def test_large_edit_does_not_exceed_process_argument_limit(tmp_path, tool):
    original = b"unique heading\n" + b"unchanged body\n" * 20000
    runtime = await editor(tmp_path, original)
    arguments = {"path": "a.txt"}
    expected = original.replace(b"unique heading", b"edited heading", 1)
    if tool == "write_file":
        arguments["content"] = expected.decode()
    else:
        arguments.update(old_text="unique heading", new_text="edited heading")
    result = await runtime.step(ToolCall(name=tool, arguments=arguments))
    assert result.status == "success", result.model_dump()
    assert (tmp_path / "a.txt").read_bytes() == expected
    assert result.changed_paths == ["a.txt"]


async def test_long_command_output_is_archived_and_readable_after_resume(tmp_path):
    runtime = Runtime(tmp_path, Settings(approval="auto", memory_enabled=False))
    runtime.start(TaskSpec(prompt="inspect output"))
    result = await runtime.step(ToolCall(name="run_shell", arguments={
        "command": "python -c 'print(\"x\" * 300000); print(\"tail-evidence\")'",
    }))
    assert result.status == "success", result
    assert len(result.content) < 8500 and "tail-evidence" not in result.content
    assert result.data["captured_bytes"] > 256 * 1024
    assert not result.data["output_truncated"]
    ref = result.data["observation"]
    runtime.prepare_request()
    resumed = Runtime(tmp_path, runtime.settings)
    resumed.resume(runtime.state.run_id)
    page = await resumed.step(ToolCall(name="observation_read", arguments={
        "observation_id": ref["id"], "offset": 300001,
    }))
    assert json.loads(page.content)["content"] == "tail-evidence\n"
    wire = json.loads(runtime._structured_history[1]["output"])
    assert wire["observation"] == ref and wire["captured_bytes"] > 256 * 1024


async def test_command_overflow_is_explicit_and_capture_is_bounded(tmp_path):
    from aster.safety.runner import OUTPUT_LIMIT

    runtime = Runtime(tmp_path, Settings(approval="auto", memory_enabled=False))
    runtime.start(TaskSpec(prompt="inspect output"))
    result = await runtime.step(ToolCall(name="run_shell", arguments={
        "command": f"python -c 'import sys; sys.stdout.write(\"x\" * {OUTPUT_LIMIT + 500})'",
    }))
    assert result.status == "success"
    assert result.data["output_bytes"] == OUTPUT_LIMIT + 500
    assert result.data["captured_bytes"] == OUTPUT_LIMIT
    assert result.data["output_truncated"]
    assert "Overflow is unavailable" in result.content
    assert result.data["observation"]["characters"] == OUTPUT_LIMIT


async def test_failed_output_archive_is_redacted(tmp_path):
    secret = "test-credential-value"
    runtime = Runtime(tmp_path, Settings(approval="auto", api_key=secret, memory_enabled=False))
    runtime.start(TaskSpec(prompt="inspect failure"))
    result = await runtime.step(ToolCall(name="run_shell", arguments={
        "command": f"python -c 'print(\"{secret}\"); raise SystemExit(2)'",
    }))
    assert result.status == "error"
    page = runtime.evidence.read_observation(runtime.state.run_id, result.data["observation"]["id"])
    assert "[REDACTED]" in page["content"] and secret not in page["content"]
    assert secret not in result.content


async def test_archive_failure_keeps_executed_command_and_changed_files(tmp_path, monkeypatch):
    runtime = Runtime(tmp_path, Settings(approval="auto", memory_enabled=False))
    runtime.start(TaskSpec(prompt="write a file"))

    def fail(*_):
        raise OSError("archive full")

    monkeypatch.setattr(runtime.evidence, "archive_observation", fail)
    call = ToolCall(name="run_shell", arguments={
        "command": "python -c 'from pathlib import Path; Path(\"done.txt\").write_text(\"done\")'"
    })
    outcome = await runtime.step(call)
    assert outcome.reason == "output_archive_unavailable" and outcome.partial_success
    assert outcome.data["execution_status"] == "success"
    assert (tmp_path / "done.txt").read_text() == "done"
    assert runtime.state.changed_paths == ["done.txt"]
    assert call.call_id in runtime.state.used_call_ids
    assert runtime.state.stop_reason == "output_archive_unavailable"
    wire = json.loads(runtime._structured_history[-1]["output"])
    assert wire["status"] == "error" and wire["changed_paths"] == ["done.txt"]
