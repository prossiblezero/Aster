import os
import subprocess

import pytest

from aster.config import Settings
from aster.core.runtime import Runtime
from aster.safety.runner import SandboxRunner
from aster.schema import TaskSpec
from aster.storage.workspace import BoundaryError, Workspace


def git(root, *arguments):
    return subprocess.run(
        ["git", "-C", str(root), *arguments], check=True, capture_output=True
    ).stdout


def ignored_project(tmp_path):
    git(tmp_path, "init", "-q")
    (tmp_path / ".gitignore").write_text("experiments/\n")
    project = tmp_path / "experiments" / "project"
    project.mkdir(parents=True)
    return project


def test_runtime_starts_in_parent_ignored_root_without_creating_a_repository(tmp_path):
    project = ignored_project(tmp_path)
    (project / "source.py").write_text("original\n")
    runtime = Runtime(project, Settings())
    runtime.start(TaskSpec(prompt="inspect the project"))
    assert set(runtime.workspace.snapshot()) == {"source.py"}
    assert runtime.workspace.path("new.py", write=True) == project / "new.py"
    assert not (project / ".git").exists()
    assert git(tmp_path, "status", "--porcelain") == b"?? .gitignore\n"


def test_ignored_root_honors_local_git_rules_tracked_files_and_boundaries(tmp_path):
    project = ignored_project(tmp_path)
    (project / ".gitignore").write_text("*.log\ncache/\n")
    (project / "source.py").write_text("source")
    (project / "ignored.log").write_text("ignored")
    (project / "tracked.log").write_text("tracked")
    (project / "cache").mkdir()
    (project / "cache" / "tracked.py").write_text("tracked")
    (project / "cache" / "ignored.py").write_text("ignored")
    (project / "nested").mkdir()
    (project / "nested" / ".gitignore").write_text("*.txt\n!keep.txt\n")
    (project / "nested" / "keep.txt").write_text("keep")
    (project / "nested" / "ignored.txt").write_text("ignored")
    (project / ".env").write_text("synthetic-secret")
    (project / "escape").symlink_to(tmp_path / ".gitignore")
    git(
        tmp_path,
        "add",
        "-f",
        "experiments/project/tracked.log",
        "experiments/project/cache/tracked.py",
        "experiments/project/.env",
    )
    # A tracked file at the parent's root must not exempt a same-named local file.
    (tmp_path / "ignored.log").touch()
    git(tmp_path, "add", "ignored.log")
    index = (tmp_path / ".git" / "index").read_bytes()
    workspace = Workspace(project)
    assert set(workspace.snapshot()) == {
        ".gitignore",
        "source.py",
        "tracked.log",
        "cache/tracked.py",
        "nested/.gitignore",
        "nested/keep.txt",
    }
    assert {p.name for p in workspace.files("cache")} == {"tracked.py"}
    assert workspace.path("tracked.log", write=True).is_file()
    assert workspace.path("cache/tracked.py", write=True).is_file()
    for name in ("ignored.log", "cache/new.py", "nested/ignored.txt"):
        with pytest.raises(BoundaryError, match="git_ignored_path"):
            workspace.path(name, write=True)
    for name, reason in (
        (".env", "sensitive_path"),
        ("../escape", "path_escape"),
        ("escape", "symlink_path"),
        (".git/config", "protected_metadata"),
    ):
        with pytest.raises(BoundaryError, match=reason):
            workspace.path(name, write=True)
    assert (tmp_path / ".git" / "index").read_bytes() == index


async def test_sandbox_in_ignored_root_publishes_sources_and_rejects_local_ignored_outputs(
    tmp_path,
):
    project = ignored_project(tmp_path)
    (project / ".gitignore").write_text("*.log\n")
    (project / "source.py").write_text("original")
    workspace = Workspace(project)
    runner = SandboxRunner(Settings())
    result = await runner.run(workspace, "printf changed > source.py")
    assert result.status == "success", result.model_dump()
    assert (project / "source.py").read_text() == "changed"
    result = await runner.run(workspace, "printf invalid > source.py; printf ignored > output.log")
    assert result.status == "blocked" and result.reason == "git_ignored_path"
    assert (project / "source.py").read_text() == "changed"
    assert not (project / "output.log").exists()


def test_ignored_root_keeps_global_and_repository_excludes_and_fails_closed(tmp_path, monkeypatch):
    project = ignored_project(tmp_path)
    excludes = tmp_path / "global-excludes"
    excludes.write_text("*.global\n")
    git(tmp_path, "config", "core.excludesFile", str(excludes))
    (tmp_path / ".git" / "info" / "exclude").write_text("*.local\n")
    for name in ("source.py", "ignored.global", "ignored.local"):
        (project / name).write_text(name)
    workspace = Workspace(project)
    assert set(workspace.snapshot()) == {"source.py"}
    for name in ("ignored.global", "ignored.local"):
        with pytest.raises(BoundaryError, match="git_ignored_path"):
            workspace.path(name, write=True)

    def failed(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 128, b"", b"unavailable")

    monkeypatch.setattr(subprocess, "run", failed)
    with pytest.raises(BoundaryError, match="git_ignore_unavailable"):
        workspace.snapshot()
    with pytest.raises(BoundaryError, match="git_ignore_unavailable"):
        workspace.path("source.py", write=True)


def test_ignored_root_still_rejects_special_files_missing_from_git_listing(tmp_path):
    project = ignored_project(tmp_path)
    (project / "nested").mkdir()
    os.mkfifo(project / "nested" / "pipe")
    with pytest.raises(BoundaryError, match="special_file"):
        Workspace(project).snapshot()
