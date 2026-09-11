import pytest

from aster.config import Settings
from aster.runtime import Runtime
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
