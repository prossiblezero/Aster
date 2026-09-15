import asyncio
import json
from pathlib import Path

import pytest

from aster.config import Settings
from aster.runtime import Runtime
from aster.schema import TaskSpec, ToolCall
from aster.tools import CORE_TOOLS, SCHEMAS

CASES = json.loads((Path(__file__).parents[1] / "datasets/tool_contract_cases.json").read_text())
VISIBILITY = json.loads(
    (Path(__file__).parents[1] / "datasets/tool_visibility_cases.json").read_text()
)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
async def test_repository_test_result_contract(tmp_path, case):
    for name, content in case["files"].items():
        (tmp_path / name).write_text(content)
    runtime = Runtime(tmp_path, Settings(approval="auto"))
    runtime.start(TaskSpec(prompt="inspect"))
    before = runtime.workspace.snapshot()
    fingerprint = runtime.workspace.fingerprint()
    outcome = await runtime.step(ToolCall(name="run_tests", arguments={"command": case["command"]}))
    assert (outcome.status, outcome.reason) == (case["status"], case["reason"]), outcome.content
    assert (runtime.state.verification.state == "passed") == (case["status"] == "success")
    assert runtime.workspace.snapshot() == before
    assert outcome.changed_paths == [] and runtime.state.changed_paths == []
    assert outcome.data["tested_workspace_hash"] == fingerprint


async def test_successful_report_cannot_verify_a_workspace_changed_during_tests(
    tmp_path, monkeypatch
):
    from aster.safety.runner import SandboxRunner

    (tmp_path / "source.txt").write_text("before")
    (tmp_path / "test_sample.py").write_text(
        "from pathlib import Path\ndef test_source():\n"
        "    assert Path('source.txt').read_text() == 'before'\n"
    )
    tested, release = asyncio.Event(), asyncio.Event()
    original_run = SandboxRunner.run

    async def pause_after_real_tests(self, *args, **kwargs):
        result = await original_run(self, *args, **kwargs)
        tested.set()
        await release.wait()
        return result

    monkeypatch.setattr(SandboxRunner, "run", pause_after_real_tests)
    runtime = Runtime(tmp_path, Settings(approval="auto"))
    runtime.start(TaskSpec(prompt="Inspect"))
    pending = asyncio.create_task(
        runtime.step(ToolCall(name="run_tests", arguments={"command": "pytest -q"}))
    )
    try:
        await asyncio.wait_for(tested.wait(), 5)
        (tmp_path / "source.txt").write_text("changed by another writer")
    finally:
        release.set()
    outcome = await pending
    assert outcome.reason == "workspace_changed_during_verification"
    assert runtime.state.verification.state != "passed"
    assert (tmp_path / "source.txt").read_text() == "changed by another writer"


@pytest.mark.parametrize("profile", ["core", "all"])
@pytest.mark.parametrize("case", VISIBILITY["cases"], ids=lambda case: case["id"])
async def test_disabled_capabilities_hidden_in_schema_and_checked_at_execution(
    tmp_path, case, profile
):
    runtime = Runtime(
        tmp_path,
        Settings.model_validate(
            {"approval": "auto", "completion": "off", **case["settings"], "tool_profile": profile}
        ),
        question_callback=(lambda _: "answer") if case.get("question_callback", True) else None,
    )
    runtime.start(TaskSpec(prompt="look around"))
    runtime.state.mode = case.get("mode", "work")
    disabled = {name for names in case["unavailable"].values() for name in names}
    available = set(SCHEMAS) - disabled
    tools = {tool["name"]: tool for tool in runtime.tool_definitions()}
    assert tools.keys() == (available if profile == "all" else available & CORE_TOOLS)
    catalog = tools["tool_load"]["parameters"]["properties"]["names"]["items"]["enum"]
    assert set(catalog) == available
    # Earlier loading (including a resumed loaded_tools list) cannot bypass configuration.
    runtime.state.loaded_tools = list(SCHEMAS)
    assert {tool["name"] for tool in runtime.tool_definitions()} == available
    runtime.state.loaded_tools = []
    if disabled:
        outcome = await runtime.step(
            ToolCall(name="tool_load", arguments={"names": ["todo_add", *sorted(disabled)]})
        )
        assert outcome.reason == "tool_unavailable" and not runtime.state.loaded_tools
    for reason, names in case["unavailable"].items():
        for name in names:
            # Each probe gets a fresh run so repeated denials don't trigger loop recovery.
            runtime.start(TaskSpec(prompt="look around"))
            runtime.state.mode = case.get("mode", "work")
            outcome = await runtime.step(
                ToolCall(name=name, arguments=VISIBILITY["probes"][name])
            )
            assert (outcome.status, outcome.reason) == ("blocked", reason)
    assert not runtime.workspace.snapshot()


async def test_plan_mode_changes_schema_and_prevents_loading_write_tools(tmp_path):
    runtime = Runtime(tmp_path, Settings(approval="auto", completion="off"))
    runtime.start(TaskSpec(prompt="look around"))
    assert "write_file" in runtime._tool_names()
    await runtime.step(ToolCall(name="plan_enter"))
    assert "write_file" not in runtime._tool_names()
    result = await runtime.step(
        ToolCall(name="tool_load", arguments={"names": ["todo_add", "write_file"]})
    )
    assert result.reason == "tool_unavailable" and not runtime.state.loaded_tools
    await runtime.step(ToolCall(name="plan_exit"))
    assert "write_file" in runtime._tool_names()


def test_empty_tool_selection_stays_empty():
    from aster.tools import definitions

    assert definitions([]) == []


@pytest.mark.parametrize(
    "command",
    [
        'python -c "print(1); print(2)"',
        'python -c "print(2 * 3); d = {1: 2}; print(d[1])"',
        'python -m pytest -k "(smoke or fast)"',
        'printf "%s" "a;b"',
        "printf '%s' '*'",
        "printf '%s' ';'",
        "printf '%s' '$name'",
        'printf "%s" "\\$name"',
    ],
)
def test_quoted_arguments_are_not_shell_operators(command):
    from aster.safety.policy import permission

    assert (
        permission(
            "run_shell", "process", {"command": command}, Settings(approval="auto"), "work"
        ).action
        == "allow"
    )
    assert (
        permission("run_shell", "process", {"command": command}, Settings(), "work").action == "ask"
    )


@pytest.mark.parametrize(
    "command",
    [
        "echo a; echo b",
        "echo a\necho b",
        "cat a | cat",
        'echo "$(id)"',
        'sh -c "echo a"',
        'env sh -c "echo a"',
        "VAR=x echo a",
        "busybox rm file.txt",
        "xargs rm",
    ],
)
def test_shell_programs_keep_approval_and_hard_denials(command):
    from aster.config import ExecRule
    from aster.safety.policy import command_rule

    assert command_rule(command, Settings(approval="auto")).action == "ask"
    settings = Settings(rules=[ExecRule(prefix=["rm"], decision="deny", reason="protected")])
    assert command_rule(command, settings).action == "deny"


def test_absolute_command_cannot_bypass_deny():
    from aster.config import ExecRule
    from aster.safety.policy import command_rule

    settings = Settings(rules=[ExecRule(prefix=["rm"], decision="deny", reason="protected")])
    assert command_rule("/bin/rm -f file.txt", settings).action == "deny"


@pytest.mark.parametrize("readonly", [False, True])
async def test_isolation_off_hides_tools_that_require_isolation(tmp_path, readonly):
    runtime = Runtime(
        tmp_path, Settings(sandbox="off", read_only=readonly, tool_profile="all", approval="auto")
    )
    runtime.start(TaskSpec(prompt="Look"))
    assert "run_tests" not in runtime._tool_names()
    assert ("run_shell" not in runtime._tool_names()) == readonly
    outcome = await runtime.step(ToolCall(name="run_tests", arguments={"command": "pytest -q"}))
    assert outcome.reason == "isolation_required_for_scope"


async def test_simple_python_command_runs_but_readonly_still_cannot_publish(tmp_path):
    (tmp_path / "input.txt").write_text("original")
    runtime = Runtime(tmp_path, Settings(approval="auto", read_only=True, completion="off"))
    runtime.start(TaskSpec(prompt="look around"))
    result = await runtime.step(
        ToolCall(name="run_shell", arguments={"command": 'python -c "print(1); print(2)"'})
    )
    assert result.status == "success" and result.content == "1\n2\n"
    result = await runtime.step(
        ToolCall(
            name="run_shell",
            arguments={
                "command": "python -c \"from pathlib import Path; Path('input.txt').write_text('changed')\""
            },
        )
    )
    assert result.reason == "read_only"
    assert (tmp_path / "input.txt").read_text() == "original"
