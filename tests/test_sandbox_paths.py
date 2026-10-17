"""Shell commands address the workspace by its real absolute path, though they run in a copy."""

from pathlib import Path

import pytest

from aster.config import Settings
from aster.core.runtime import Runtime
from aster.safety.runner import map_root
from aster.schema import TaskSpec, ToolCall

pytestmark = pytest.mark.core


def test_map_root_only_rewrites_whole_path_components():
    source, target = Path("/app"), Path("/tmp/copy")
    assert map_root("cd /app && python3 /app/x.py '/app'", source, target) == (
        "cd /tmp/copy && python3 /tmp/copy/x.py '/tmp/copy'")
    unchanged = "ls /apps /app-old /x/app ./app ~/app /app.bak"
    assert map_root(unchanged, source, target) == unchanged


@pytest.mark.asyncio
async def test_absolute_workspace_paths_read_and_write_through_the_sandbox(tmp_path):
    root = tmp_path.resolve()
    (root / "interp.py").write_text("print('hello')\n")
    runtime = Runtime(root, Settings(approval="auto", memory_enabled=False, sandbox="required"),
                      approval_callback=lambda *_: True)
    runtime.start(TaskSpec(prompt="run interp.py"))
    result = await runtime.step(ToolCall(name="run_shell", arguments={
        "command": f"cd {root} && python3 {root}/interp.py > {root}/out.txt && pwd"}))
    assert result.status == "success", result
    assert result.content.strip() == str(root)
    assert result.changed_paths == ["out.txt"]
    assert (root / "out.txt").read_text() == "hello\n"


@pytest.mark.asyncio
async def test_absolute_paths_in_test_commands_target_the_test_copy(tmp_path):
    root = tmp_path.resolve()
    (root / "test_ok.py").write_text("def test_ok():\n    assert True\n")
    runtime = Runtime(root, Settings(approval="auto", memory_enabled=False, sandbox="required"))
    runtime.start(TaskSpec(prompt="verify", require_verification=True))
    result = await runtime.step(ToolCall(name="run_tests", arguments={
        "command": f"python -m pytest -q {root}/test_ok.py"}))
    assert result.status == "success", result
    assert result.data["test_results"] == {"pytest: test_ok::test_ok": "passed"}
