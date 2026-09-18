import asyncio
import shlex
import subprocess

import pytest

from aster.config import ExecRule, Settings
from aster.runtime import Runtime
from aster.safety.policy import command_rule, shell_argv
from aster.schema import ModelReply, TaskSpec, ToolCall


def test_small_command_grammar_matches_shell_arguments():
    import json
    import random
    import sys

    rng = random.Random(7)
    head = shlex.join([sys.executable, "-c", "import sys,json;print(json.dumps(sys.argv[1:]))"])
    alphabet = "ab \\'\"$`;\t\r"
    checked = 0
    for _ in range(1000):
        command = head + " " + "".join(rng.choices(alphabet, k=rng.randint(1, 16)))
        try:
            parsed = shell_argv(command)
        except ValueError:
            continue
        if parsed is not None:
            actual = json.loads(subprocess.check_output(["/bin/sh", "-c", command], text=True))
            assert parsed[3:] == actual, command
            checked += 1
    assert checked >= 20


def test_no_approval_means_unconditional_write_capabilities_are_hidden(tmp_path):
    from aster.config import ExecRule

    runtime = Runtime(tmp_path, Settings(tool_profile="all"))
    runtime.start(TaskSpec(prompt="Look"))
    assert not {"write_file", "patch_file", "plan_exit", "run_shell", "run_tests"} & set(
        runtime._tool_names()
    )
    settings = Settings(
        approval="never",
        tool_profile="all",
        rules=[ExecRule(prefix=["python", "-m", "pytest"], decision="allow", reason="tests")],
    )
    allowed = Runtime(tmp_path, settings)
    assert "run_tests" in allowed._tool_names()
    assert "write_file" not in allowed._tool_names()


async def test_tool_availability_tracks_current_approval_callback(tmp_path):
    runtime = Runtime(tmp_path, Settings(completion="off"))
    runtime.start(TaskSpec(prompt="Demo"))
    assert "write_file" not in runtime._tool_names()
    runtime.approval_callback = lambda *_: True
    assert "write_file" in runtime._tool_names()
    result = await runtime.step(
        ToolCall(name="write_file", arguments={"path": "new.txt", "content": "approved"})
    )
    assert result.status == "success"
    runtime.approval_callback = None
    assert "write_file" not in runtime._tool_names()
    denied = await runtime.step(
        ToolCall(name="write_file", arguments={"path": "other.txt", "content": "denied"})
    )
    assert denied.reason == "approval_unavailable" and not (tmp_path / "other.txt").exists()


def test_dot_source_command_keeps_nested_command_denials():
    settings = Settings(
        approval="auto", rules=[ExecRule(prefix=["rm"], decision="deny", reason="protected")]
    )
    assert shell_argv(". ./script.sh") is None
    assert command_rule(". ./script.sh", settings).action == "deny"


def test_command_rules_only_expose_tools_they_can_authorize(tmp_path):
    runtime = Runtime(
        tmp_path,
        Settings(rules=[ExecRule(prefix=["echo"], decision="allow", reason="echo only")]),
    )
    assert "run_shell" in runtime._tool_names()
    assert "run_tests" not in runtime._tool_names()
    runtime.settings.rules = [ExecRule(prefix=["python"], decision="allow", reason="python")]
    assert "run_tests" in runtime._tool_names()
    runtime.settings.rules.append(ExecRule(prefix=["python"], decision="deny", reason="denied"))
    assert not {"run_shell", "run_tests"} & set(runtime._tool_names())


@pytest.mark.parametrize("literal", ["$name", "`name`", r"\$name"])
def test_quoted_rule_parameters_match_actual_shell(literal):
    if literal.startswith("\\"):
        command = "printf '%s' " + shlex.quote(literal)
    else:
        quoted = literal.replace("$", r"\$").replace("`", r"\`")
        command = 'printf "%s" "' + quoted + '"'
    observed = subprocess.check_output(["/bin/sh", "-c", command], text=True)
    assert shell_argv(command) == ["printf", "%s", observed]
    settings = Settings(
        approval="auto",
        rules=[ExecRule(prefix=["printf", "%s", observed], decision="deny", reason="protected")],
    )
    assert command_rule(command, settings).action == "deny"


async def test_entering_plan_stops_existing_writable_workers(tmp_path):
    entered = asyncio.Event()

    class Model:
        async def complete(self, *_):
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                return ModelReply(
                    calls=[
                        ToolCall(
                            name="write_file", arguments={"path": "late.txt", "content": "bad"}
                        )
                    ]
                )

    runtime = Runtime(tmp_path, Settings(approval="auto", max_workers=2), model=Model())
    runtime.start(TaskSpec(prompt="Demo"))
    response = await runtime.workers.start("Demo", mode="worker", write_scope=["late.txt"])
    await asyncio.wait_for(entered.wait(), 2)
    result = await asyncio.wait_for(runtime.step(ToolCall(name="plan_enter")), 2)
    worker = runtime.workers.workers[response["worker_id"]]
    assert result.status == "success" and runtime.state.mode == "plan"
    assert worker.future.done() and worker.runtime.state.terminal
    assert not (tmp_path / "late.txt").exists()
    runtime.abort()
