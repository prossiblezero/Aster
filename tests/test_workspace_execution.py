import os
import shlex

import pytest

from aster.config import Settings
from aster.safety.runner import SandboxRunner
from aster.storage.workspace import BoundaryError, Workspace


def python_command(program):
    return "python -c " + shlex.quote(program)


def test_workspace_rejects_special_files_before_reading(tmp_path):
    os.mkfifo(tmp_path / "pipe")
    workspace = Workspace(tmp_path)
    with pytest.raises(BoundaryError, match="special_file"):
        workspace.path("pipe")
    with pytest.raises(BoundaryError, match="special_file"):
        workspace.files()


async def test_special_sandbox_output_is_rejected_before_snapshot(tmp_path, monkeypatch):
    original = Workspace.snapshot

    def guarded_snapshot(workspace):
        assert not any(path.is_fifo() for path in workspace.root.rglob("*")), (
            "special output reached snapshot"
        )
        return original(workspace)

    monkeypatch.setattr(Workspace, "snapshot", guarded_snapshot)
    result = await SandboxRunner(Settings()).run(Workspace(tmp_path), "mkfifo pipe")
    assert result.status == "blocked" and result.reason == "sandbox_protected_output"
    assert not (tmp_path / "pipe").exists()


@pytest.mark.parametrize("directory_to_file", [False, True])
async def test_sandbox_reconciles_file_directory_replacements(tmp_path, directory_to_file):
    target = tmp_path / "node"
    if directory_to_file:
        (target / "nested").mkdir(parents=True)
        (target / "nested" / "old.txt").write_text("old")
        program = "import shutil; shutil.rmtree('node'); Path('node').write_text('new')"
    else:
        target.write_text("old")
        program = "Path('node').unlink(); Path('node').mkdir(); Path('node/new.txt').write_text('new')"
    result = await SandboxRunner(Settings()).run(
        Workspace(tmp_path), python_command("from pathlib import Path; " + program)
    )
    assert result.status == "success", result.model_dump()
    assert (target if directory_to_file else target / "new.txt").read_text() == "new"
    assert "node" in result.changed_paths
    assert len(result.changed_paths) == 2


async def test_directory_replacement_preserves_untracked_protected_content(tmp_path):
    (tmp_path / "node").mkdir()
    (tmp_path / "node" / "old.txt").write_text("old")
    (tmp_path / "node" / ".env").write_text("synthetic-private-placeholder")
    (tmp_path / "a.txt").write_text("old")
    program = (
        "from pathlib import Path; import shutil; Path('a.txt').write_text('new'); "
        "shutil.rmtree('node'); Path('node').write_text('new')"
    )
    result = await SandboxRunner(Settings()).run(Workspace(tmp_path), python_command(program))
    assert result.status == "blocked" and result.reason == "directory_not_empty"
    assert (tmp_path / "a.txt").read_text() == "old"
    assert (tmp_path / "node" / ".env").read_text() == "synthetic-private-placeholder"
    assert not result.changed_paths and not result.partial_success


async def test_publish_error_reports_actual_partial_changes(tmp_path, monkeypatch):
    from aster.safety import runner as runner_module

    for name in ("a.txt", "b.txt"):
        (tmp_path / name).write_text("old")
    replace = os.replace

    def fail_second(source, target):
        if str(target) == str(tmp_path / "b.txt"):
            raise OSError("synthetic disk error")
        return replace(source, target)

    monkeypatch.setattr(runner_module.os, "replace", fail_second)
    command = python_command(
        "from pathlib import Path; Path('a.txt').write_text('new'); Path('b.txt').write_text('new')"
    )
    result = await SandboxRunner(Settings()).run(Workspace(tmp_path), command)
    assert result.status == "error" and result.reason == "workspace_publish_failed"
    assert result.changed_paths == ["a.txt"] and result.partial_success
    assert (tmp_path / "a.txt").read_text() == "new"
    assert (tmp_path / "b.txt").read_text() == "old"


@pytest.mark.parametrize("readonly,scope", [(True, None), (False, ["allowed.txt"])])
async def test_replacement_preflight_preserves_readonly_and_scope(tmp_path, readonly, scope):
    (tmp_path / "node").write_text("old")
    command = python_command(
        "from pathlib import Path; Path('node').unlink(); Path('node').mkdir(); "
        "Path('node/new.txt').write_text('new')"
    )
    result = await SandboxRunner(Settings(write_scope=scope)).run(
        Workspace(tmp_path), command, readonly=readonly
    )
    assert result.status == "blocked"
    assert (tmp_path / "node").read_text() == "old"
    assert not result.changed_paths


@pytest.mark.parametrize("conflicting", [False, True])
async def test_publish_compares_only_its_targets_and_reports_only_its_changes(tmp_path, conflicting):
    for name in ("a.txt", "b.txt"):
        (tmp_path / name).write_text("old")
    runner = SandboxRunner(Settings())
    process = runner._process

    async def external_editor(*args):
        result = await process(*args)
        (tmp_path / ("a.txt" if conflicting else "b.txt")).write_text("external")
        return result

    runner._process = external_editor
    result = await runner.run(Workspace(tmp_path), "printf new > a.txt")
    if conflicting:
        assert result.status == "error" and result.reason == "concurrent_workspace_change"
        assert result.changed_paths == []
        assert (tmp_path / "a.txt").read_text() == "external"
    else:
        assert result.status == "success" and result.changed_paths == ["a.txt"]
        assert (tmp_path / "a.txt").read_text() == "new"
        assert (tmp_path / "b.txt").read_text() == "external"


async def test_symlink_output_rejects_the_entire_batch(tmp_path):
    (tmp_path / "a.txt").write_text("old")
    result = await SandboxRunner(Settings()).run(
        Workspace(tmp_path), "printf new > a.txt; ln -s a.txt link"
    )
    assert result.status == "blocked" and result.reason == "sandbox_protected_output"
    assert result.changed_paths == [] and not result.partial_success
    assert (tmp_path / "a.txt").read_text() == "old"
    assert not (tmp_path / "link").is_symlink()


async def test_partial_publication_updates_runtime_state(tmp_path, monkeypatch):
    from aster.core.runtime import Runtime
    from aster.schema import TaskSpec, ToolCall

    for name in ("a.txt", "b.txt"):
        (tmp_path / name).write_text("old")
    (tmp_path / "change.sh").write_text("printf new > a.txt\nprintf new > b.txt\n")
    runtime = Runtime(
        tmp_path, Settings(approval="auto"), approval_callback=lambda _call, _decision: True
    )
    runtime.start(TaskSpec(prompt="inspect files"))
    await runtime.step(ToolCall(name="read_file", arguments={"path": "a.txt"}))
    replace = os.replace

    def fail_second(source, target):
        if str(target) == str(tmp_path / "b.txt"):
            raise OSError("synthetic disk error")
        return replace(source, target)

    monkeypatch.setattr(os, "replace", fail_second)
    result = await runtime.step(ToolCall(name="run_shell", arguments={"command": "sh change.sh"}))
    assert result.status == "error" and result.partial_success
    assert result.changed_paths == runtime.state.changed_paths == ["a.txt"]
    assert "a.txt" not in runtime.state.working_memory
    assert "a.txt" not in runtime.state.read_evidence


def test_workspace_snapshot_and_diff_track_permissions_not_mtime(tmp_path):
    path = tmp_path / "script.sh"
    path.write_text("true\n")
    path.chmod(0o644)
    workspace = Workspace(tmp_path)
    baseline, text, modes = workspace.snapshot(), workspace.text_snapshot(), {"script.sh": 0o644}
    stamp = path.stat().st_mtime + 60
    os.utime(path, (stamp, stamp))
    assert workspace.snapshot() == baseline
    path.chmod(0o755)
    assert Workspace.changes(baseline, workspace.snapshot()) == ["script.sh"]
    diff = workspace.diff(text, modes)
    assert "old mode 100644" in diff and "new mode 100755" in diff


async def test_chmod_publishes_execution_bit_and_respects_readonly(tmp_path):
    path = tmp_path / "script.sh"
    path.write_text("true\n")
    path.chmod(0o644)
    runner = SandboxRunner(Settings())
    workspace = Workspace(tmp_path)
    blocked = await runner.run(workspace, "chmod +x script.sh", readonly=True)
    assert blocked.status == "blocked" and path.stat().st_mode & 0o111 == 0
    result = await runner.run(workspace, "chmod +x script.sh")
    assert result.status == "success" and result.changed_paths == ["script.sh"]
    assert path.stat().st_mode & 0o111 == 0o111
    assert workspace.mode_snapshot() == {"script.sh": 0o755}
