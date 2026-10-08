import shlex

import pytest

from aster.config import ExecRule, Settings
from aster.safety.policy import command_rule
from aster.safety.runner import SandboxRunner
from aster.storage.workspace import BoundaryError, Workspace

pytestmark = pytest.mark.core


@pytest.mark.smoke
@pytest.mark.parametrize(
    "path", ["../escape", ".aster/trace.json", ".git/config", ".env", "keys/a.pem"]
)
def test_path_boundary(tmp_path, path):
    with pytest.raises(BoundaryError):
        Workspace(tmp_path).path(path)


def test_symlink(tmp_path):
    (tmp_path / "link").symlink_to("/etc/passwd")
    with pytest.raises(BoundaryError):
        Workspace(tmp_path).path("link")


@pytest.mark.smoke
def test_rules_deny_precedence():
    settings = Settings(
        rules=[
            ExecRule(prefix=["git"], decision="allow", reason="git"),
            ExecRule(prefix=["git", "push"], decision="deny", reason="external write"),
        ]
    )
    assert command_rule("git push origin main", settings).action == "deny"
    assert command_rule("git status; echo x", settings).action == "deny"
    assert command_rule("git status; echo x", Settings()).action == "ask"


@pytest.mark.smoke
async def test_real_isolation_and_sync(tmp_path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    secret = tmp_path / "host_secret.txt"
    secret.write_text("fake-secret")
    (workspace / "a.py").write_text("original")
    runner = SandboxRunner(Settings(approval="auto"))
    result = await runner.run(Workspace(workspace), "printf changed > a.py")
    assert result.status == "success", result.model_dump()
    assert result.data["isolation"] == "landlock+seccomp"
    assert (workspace / "a.py").read_text() == "changed"
    result = await runner.run(Workspace(workspace), f"cat {shlex.quote(str(secret))}")
    assert result.status == "error"
    assert "fake-secret" not in result.content
    result = await runner.run(Workspace(workspace), "python -c 'import socket; socket.socket()'")
    assert result.status == "error"
    assert "Operation not permitted" in result.content


async def test_timeout_partial_success(tmp_path):
    runner = SandboxRunner(Settings(approval="auto"))
    result = await runner.run(Workspace(tmp_path), "echo partial > made.txt; sleep 5", 0.3)
    assert result.status == "timeout"
    assert result.partial_success
    assert (tmp_path / "made.txt").exists()


async def test_protected_staged_write_rejected(tmp_path):
    runner = SandboxRunner(Settings(approval="auto"))
    result = await runner.run(Workspace(tmp_path), "mkdir .aster; echo forged > .aster/a.json")
    assert result.status == "blocked"
    assert not (tmp_path / ".aster").exists()
