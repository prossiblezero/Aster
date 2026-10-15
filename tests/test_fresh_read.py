import pytest

from aster.config import Settings
from aster.core.runtime import Runtime
from aster.schema import TaskSpec, ToolCall


@pytest.mark.asyncio
async def test_existing_file_edit_requires_fresh_read(tmp_path):
    (tmp_path / "a.txt").write_text("old")
    runtime = Runtime(tmp_path, Settings(approval="auto"))
    runtime.start(TaskSpec(prompt="edit a.txt"))
    blocked = await runtime.step(
        ToolCall(
            name="patch_file", arguments={"path": "a.txt", "old_text": "old", "new_text": "new"}
        )
    )
    assert blocked.reason == "fresh_read_required"
    read = await runtime.step(ToolCall(name="read_file", arguments={"path": "a.txt"}))
    assert read.status == "success"
    changed = await runtime.step(
        ToolCall(
            name="patch_file", arguments={"path": "a.txt", "old_text": "old", "new_text": "new"}
        )
    )
    assert changed.status == "success"
    again = await runtime.step(ToolCall(name="patch_file", arguments={
        "path": "a.txt", "old_text": "new", "new_text": "newer",
    }))
    assert again.status == "success"
    assert (tmp_path / "a.txt").read_text() == "newer"


@pytest.mark.parametrize("external", ["direct", "shell"])
async def test_external_change_after_own_edit_requires_read(tmp_path, external):
    path = tmp_path / "a.txt"
    runtime = Runtime(tmp_path, Settings(approval="auto", memory_enabled=False))
    runtime.start(TaskSpec(prompt="edit"))
    created = await runtime.step(ToolCall(name="write_file", arguments={
        "path": "a.txt", "content": "own",
    }))
    assert created.status == "success"
    if external == "direct":
        path.write_text("external")
    else:
        result = await runtime.step(ToolCall(name="run_shell", arguments={
            "command": "python -c 'from pathlib import Path; Path(\"a.txt\").write_text(\"external\")'",
        }))
        assert result.status == "success"
    blocked = await runtime.step(ToolCall(name="patch_file", arguments={
        "path": "a.txt", "old_text": "external", "new_text": "overwritten",
    }))
    assert blocked.reason == "fresh_read_required"
    assert path.read_text() == "external"


async def test_create_then_patch_and_same_batch_edits_need_no_reread(tmp_path):
    runtime = Runtime(tmp_path, Settings(approval="auto", memory_enabled=False))
    runtime.start(TaskSpec(prompt="edit"))
    created = await runtime.step(ToolCall(name="write_file", arguments={
        "path": "a.txt", "content": "alpha\nbeta\n",
    }))
    assert created.status == "success"
    results = await runtime._execute_batch([
        ToolCall(name="patch_file", arguments={
            "path": "a.txt", "old_text": "alpha", "new_text": "first",
        }),
        ToolCall(name="patch_file", arguments={
            "path": "a.txt", "old_text": "beta", "new_text": "second",
        }),
    ])
    assert [r.status for r in results] == ["success", "success"]
    assert (tmp_path / "a.txt").read_text() == "first\nsecond\n"
    assert "a.txt" not in runtime.state.working_memory


@pytest.mark.parametrize("tool", ["write_file", "patch_file"])
async def test_change_after_successful_write_before_collection_does_not_grant_evidence(tmp_path, monkeypatch, tool):
    path = tmp_path / "a.txt"
    path.write_text("old")
    runtime = Runtime(tmp_path, Settings(approval="auto", memory_enabled=False))
    runtime.start(TaskSpec(prompt="edit"))
    await runtime.step(ToolCall(name="read_file", arguments={"path": "a.txt"}))
    run = runtime.sandbox.run

    async def raced(*args, **kwargs):
        result = await run(*args, **kwargs)
        assert result.status == "success"
        path.write_text("external")
        return result

    monkeypatch.setattr(runtime.sandbox, "run", raced)
    args = {"path": "a.txt"}
    args.update(content="own" if tool == "write_file" else None)
    if tool == "patch_file":
        args = {"path": "a.txt", "old_text": "old", "new_text": "own"}
    await runtime.step(ToolCall(name=tool, arguments=args))
    assert "a.txt" not in runtime.state.read_evidence
    blocked = await runtime.step(ToolCall(name="patch_file", arguments={
        "path": "a.txt", "old_text": "external", "new_text": "overwritten",
    }))
    assert blocked.reason == "fresh_read_required"
