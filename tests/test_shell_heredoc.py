"""Shell program classification must not tokenize literal heredoc bodies as argv."""

import pytest

from aster.config import ExecRule, Settings
from aster.core.runtime import Runtime
from aster.safety.policy import command_rule, permission, shell_argv
from aster.schema import TaskSpec, ToolCall


@pytest.mark.parametrize(
    "command",
    [
        "cat <<'EOF'\ndirectory's contents\nEOF\n",
        'cat <<"EOF"\n"unmatched quotation\nEOF\n',
        "cat <<EOF\ndirectory's contents\nEOF\n",
        "cat <<-'EOF'\n\tdirectory's contents\n\tEOF\n",
    ],
)
def test_heredoc_keeps_compound_approval_and_hard_denials(command):
    assert shell_argv(command) is None
    decision = command_rule(command, Settings(approval="auto"))
    assert (decision.action, decision.reason) == ("ask", "compound_shell")
    assert (
        permission(
            "run_shell", "process", {"command": command}, Settings(approval="auto"), "work"
        ).action
        == "ask"
    )
    settings = Settings(
        approval="auto",
        rules=[ExecRule(prefix=["rm"], decision="deny", reason="protected")],
    )
    decision = command_rule(command, settings)
    assert (decision.action, decision.reason) == ("deny", "compound_shell_with_deny_rules")


@pytest.mark.parametrize("command", ["", "  ", "echo 'unclosed", "echo \x00", "echo \\\n\x00"])
def test_invalid_simple_shell_remains_denied(command):
    decision = command_rule(command, Settings(approval="auto"))
    assert (decision.action, decision.reason) == ("deny", "invalid_shell")


def test_quoted_heredoc_text_is_still_an_ordinary_argument():
    assert shell_argv("printf '%s' \"<<'EOF'\"") == ["printf", "%s", "<<'EOF'"]


@pytest.mark.parametrize("prefix", [
    "PYTHONPATH=.", "PYTHONPATH=./src", "PYTHONPATH=.:src:lib/local",
])
def test_local_pythonpath_preserves_command_rules(prefix):
    command = prefix + " python -m pytest"
    assert shell_argv(command) == ["python", "-m", "pytest"]
    assert permission("run_shell", "process", {"command": command}, Settings(approval="auto"), "work").action == "allow"
    settings = Settings(rules=[ExecRule(prefix=["python", "-m", "pytest"], decision="deny", reason="protected")])
    assert command_rule(command, settings).reason == "protected"
    assert command_rule(prefix + " python -c 'print(1)'", settings).action == "deny"


@pytest.mark.parametrize("command", [
    "PYTHONPATH= python check.py", "PYTHONPATH=.: python check.py",
    "PYTHONPATH=../outside python check.py", "PYTHONPATH=/tmp python check.py",
    "PYTHONPATH=.git python check.py", "PYTHONPATH=.env python check.py",
    "PYTHONPATH=. PATH=/tmp python check.py", "PYTHONPATH=. sh check.sh",
    "PYTHONPATH=$HOME python check.py", "PYTHONPATH=. python check.py; echo done",
    "LD_PRELOAD=local.so python check.py",
])
def test_other_environment_prefixes_still_require_approval(command):
    assert command_rule(command, Settings(approval="auto")).action == "ask"


async def test_local_pythonpath_imports_work_in_shell_and_formal_tests(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "local_project.py").write_text("VALUE = 42\n")
    (tmp_path / "test_local.py").write_text(
        "from local_project import VALUE\ndef test_value(): assert VALUE == 42\n"
    )
    runtime = Runtime(tmp_path, Settings(approval="auto", memory_enabled=False))
    runtime.start(TaskSpec(prompt="Verify local imports"))
    result = await runtime.step(ToolCall(name="run_shell", arguments={
        "command": "PYTHONPATH=src python -c 'from local_project import VALUE; assert VALUE == 42'",
    }))
    assert result.status == "success", result.model_dump()
    result = await runtime.step(ToolCall(name="run_tests", arguments={
        "command": "PYTHONPATH=src python -m pytest -q",
    }))
    assert result.status == "success" and result.data["passed_tests"] == 1, result.model_dump()


async def test_approved_heredoc_runs_through_runtime_sandbox(tmp_path):
    approvals = []

    def approve(call, decision):
        approvals.append((call.name, decision.reason))
        return True

    runtime = Runtime(tmp_path, Settings(approval="auto"), approval_callback=approve)
    runtime.start(TaskSpec(prompt="Print a literal heredoc"))
    result = await runtime.step(
        ToolCall(
            name="run_shell", arguments={"command": "cat <<'EOF'\ndirectory's contents\nEOF\n"}
        )
    )
    assert result.status == "success", result.model_dump()
    assert result.content == "directory's contents\n"
    assert result.data["isolation"] == "landlock+seccomp"
    assert approvals == [("run_shell", "compound_shell")]
