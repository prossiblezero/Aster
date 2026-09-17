import subprocess

import pytest

from aster.config import Settings
from aster.safety.runner import SandboxRunner
from aster.workspace import BoundaryError, Workspace


def git(root, *arguments):
    subprocess.run(["git", "-C", str(root), *arguments], check=True, capture_output=True)


def test_git_ignores_keep_tracked_and_new_sources_and_honor_nested_rules(tmp_path):
    git(tmp_path, "init", "-q")
    (tmp_path / ".gitignore").write_text("artifacts/\n*.log\n")
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "artifacts/tracked.log").write_text("tracked")
    (tmp_path / "artifacts/ignored.log").write_text("ignored")
    (tmp_path / "website").mkdir()
    (tmp_path / "website/.gitignore").write_text("*.html\n!index.html\n")
    (tmp_path / "website/index.html").write_text("new site")
    (tmp_path / "website/generated.html").write_text("ignored")
    (tmp_path / ".env").write_text("synthetic-secret")
    git(tmp_path, "add", "-f", "artifacts/tracked.log", ".env")

    workspace = Workspace(tmp_path)
    expected = {".gitignore", "artifacts/tracked.log", "website/.gitignore", "website/index.html"}
    assert set(workspace.snapshot()) == expected
    assert set(Workspace(tmp_path / "website").snapshot()) == {".gitignore", "index.html"}
    assert {p.name for p in workspace.files("website")} == {".gitignore", "index.html"}
    assert workspace.read("artifacts/ignored.log") == "ignored"
    assert workspace.path("artifacts/tracked.log", write=True).is_file()
    with pytest.raises(BoundaryError, match="git_ignored_path"):
        workspace.path("artifacts/ignored.log", write=True)


def test_ignored_artifacts_do_not_exhaust_the_workspace_limit(tmp_path):
    git(tmp_path, "init", "-q")
    (tmp_path / ".gitignore").write_text("artifacts/\n")
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    for number in range(10001):
        (artifacts / str(number)).touch()
    (tmp_path / "source.py").write_text("pass\n")
    workspace = Workspace(tmp_path)
    assert set(workspace.snapshot()) == {".gitignore", "source.py"}
    assert workspace.files("artifacts") == []


@pytest.mark.parametrize("git_missing", [False, True])
def test_non_git_or_missing_git_keeps_the_existing_scan(tmp_path, monkeypatch, git_missing):
    (tmp_path / ".gitignore").write_text("output.log\n")
    (tmp_path / "output.log").write_text("ordinary file")
    (tmp_path / ".env").write_text("synthetic-secret")
    if git_missing:
        git(tmp_path, "init", "-q")

        def unavailable(*args, **kwargs):
            raise FileNotFoundError("git unavailable")

        monkeypatch.setattr(subprocess, "run", unavailable)
    workspace = Workspace(tmp_path)
    assert set(workspace.snapshot()) == {".gitignore", "output.log"}
    assert workspace.path("output.log", write=True).is_file()


def test_git_failure_does_not_silently_allow_an_ignored_write(tmp_path, monkeypatch):
    git(tmp_path, "init", "-q")
    (tmp_path / ".gitignore").write_text("*.log\n")
    workspace = Workspace(tmp_path)

    def failed(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 128, b"", b"unavailable")

    monkeypatch.setattr(subprocess, "run", failed)
    with pytest.raises(BoundaryError, match="git_ignore_unavailable"):
        workspace.snapshot()
    with pytest.raises(BoundaryError, match="git_ignore_unavailable"):
        workspace.path("output.log", write=True)


@pytest.mark.parametrize("existing", [False, True])
async def test_sandbox_rejects_ignored_outputs_before_publishing_any_change(tmp_path, existing):
    git(tmp_path, "init", "-q")
    (tmp_path / ".gitignore").write_text("*.log\n")
    (tmp_path / "source.py").write_text("original")
    ignored = tmp_path / "output.log"
    if existing:
        ignored.write_text("external data")
    result = await SandboxRunner(Settings()).run(
        Workspace(tmp_path), "printf changed > source.py; printf generated > output.log"
    )
    assert result.status == "blocked" and result.reason == "git_ignored_path"
    assert result.changed_paths == []
    assert (tmp_path / "source.py").read_text() == "original"
    if existing:
        assert ignored.read_text() == "external data"
    else:
        assert not ignored.exists()
