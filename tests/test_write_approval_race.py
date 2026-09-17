"""An approval authorizes the observed file version, not an editor's later changes."""

import asyncio

import pytest

from aster.config import Settings
from aster.runtime import Runtime
from aster.schema import TaskSpec, ToolCall


@pytest.mark.parametrize("sandbox", ["required", "off"])
@pytest.mark.parametrize(
    "tool,existing", [("write_file", True), ("patch_file", True), ("write_file", False)]
)
async def test_edit_during_approval_preserves_external_content(tmp_path, sandbox, tool, existing):
    path = tmp_path / "answer.txt"
    if existing:
        path.write_text("old\n")
    waiting, approved = asyncio.Event(), asyncio.Event()

    async def approve(*_):
        waiting.set()
        await approved.wait()
        return True

    runtime = Runtime(tmp_path, Settings(sandbox=sandbox), approval_callback=approve)
    runtime.start(TaskSpec(prompt="Update answer.txt"))
    if existing:
        await runtime.step(ToolCall(name="read_file", arguments={"path": "answer.txt"}))
    arguments = {"path": "answer.txt"}
    arguments.update(
        {"content": "agent\n"} if tool == "write_file" else {"old_text": "old", "new_text": "agent"}
    )
    pending = asyncio.create_task(runtime.step(ToolCall(name=tool, arguments=arguments)))
    await waiting.wait()
    path.write_text("old\nuser added this while waiting\n")
    approved.set()
    result = await pending

    assert path.read_text() == "old\nuser added this while waiting\n"
    assert result.status == "blocked" and result.reason == "fresh_read_required"
    assert result.changed_paths == []
    assert runtime.state.changed_paths == []


async def test_target_changed_while_copying_is_not_overwritten(tmp_path, monkeypatch):
    from aster.safety import runner as runner_module

    path = tmp_path / "answer.txt"
    path.write_text("old\n")
    runtime = Runtime(tmp_path, Settings(approval="auto"))
    runtime.start(TaskSpec(prompt="Update answer.txt"))
    await runtime.step(ToolCall(name="read_file", arguments={"path": "answer.txt"}))
    copy = runner_module.shutil.copy2

    def external_edit_before_copy(source, target, *args, **kwargs):
        if source == path:
            path.write_text("external edit during copy\n")
        return copy(source, target, *args, **kwargs)

    monkeypatch.setattr(runner_module.shutil, "copy2", external_edit_before_copy)
    result = await runtime.step(
        ToolCall(name="write_file", arguments={"path": "answer.txt", "content": "agent\n"})
    )
    assert path.read_text() == "external edit during copy\n"
    assert result.status == "blocked" and result.reason == "fresh_read_required"
    assert result.changed_paths == []


async def test_expected_file_version_survives_best_effort_fallback(tmp_path, monkeypatch):
    from aster.schema import Outcome

    path = tmp_path / "answer.txt"
    path.write_text("old\n")
    runtime = Runtime(tmp_path, Settings(approval="auto", sandbox="best_effort"))
    runtime.start(TaskSpec(prompt="Update answer.txt"))
    await runtime.step(ToolCall(name="read_file", arguments={"path": "answer.txt"}))
    process = runtime.sandbox._process

    async def sandbox_unavailable(argv, cwd, timeout):
        if cwd != tmp_path:
            path.write_text("external edit before fallback\n")
            return Outcome(status="error", exit_code=125, content="ASTER_SANDBOX_UNAVAILABLE")
        return await process(argv, cwd, timeout)

    monkeypatch.setattr(runtime.sandbox, "_process", sandbox_unavailable)
    result = await runtime.step(
        ToolCall(name="write_file", arguments={"path": "answer.txt", "content": "agent\n"})
    )
    assert path.read_text() == "external edit before fallback\n"
    assert result.status == "blocked" and result.reason == "fresh_read_required"
    assert result.changed_paths == []


@pytest.mark.parametrize("tool", ["write_file", "patch_file"])
@pytest.mark.parametrize("newline", [b"\r\n", b"\r"])
async def test_newline_only_change_during_approval_is_preserved(tmp_path, tool, newline):
    path = tmp_path / "answer.txt"
    path.write_bytes(b"old\nsecond line\n")
    external = b"old" + newline + b"second line" + newline

    def approve(*_):
        path.write_bytes(external)
        return True

    runtime = Runtime(tmp_path, Settings(), approval_callback=approve)
    runtime.start(TaskSpec(prompt="Update answer.txt"))
    await runtime.step(ToolCall(name="read_file", arguments={"path": "answer.txt"}))
    arguments = {"path": "answer.txt"}
    arguments.update(
        {"content": "agent\n"} if tool == "write_file" else {"old_text": "old", "new_text": "agent"}
    )
    result = await runtime.step(ToolCall(name=tool, arguments=arguments))
    assert path.read_bytes() == external
    assert result.status == "blocked" and result.reason == "fresh_read_required"
    assert not result.changed_paths


async def test_unchanged_crlf_file_still_accepts_a_patch(tmp_path):
    path = tmp_path / "answer.txt"
    path.write_bytes(b"old\r\nsecond line\r\n")
    runtime = Runtime(tmp_path, Settings(approval="auto"))
    runtime.start(TaskSpec(prompt="Update answer.txt"))
    await runtime.step(ToolCall(name="read_file", arguments={"path": "answer.txt"}))
    result = await runtime.step(
        ToolCall(
            name="patch_file",
            arguments={"path": "answer.txt", "old_text": "old", "new_text": "agent"},
        )
    )
    assert result.status == "success"
    assert path.read_text() == "agent\nsecond line\n"
