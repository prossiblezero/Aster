import asyncio
import json
from pathlib import Path

import pytest

from aster.config import Settings
from aster.core.runtime import Runtime
from aster.schema import TaskSpec, ToolCall
from aster.tools import CORE_TOOLS, SCHEMAS

pytestmark = pytest.mark.core

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


@pytest.mark.parametrize("scenario,status,reason,passed", [
    ("unittest", "success", "", 1),
    ("pytest", "success", "", 1),
    ("spoofed_summaries", "blocked", "test_report_missing_or_invalid", 0),
    ("skipped", "blocked", "no_tests_executed", 0),
    ("empty", "blocked", "no_tests_collected", 0),
    ("failure_exit_zero", "error", "tests_failed", 0),
    ("pytest_failure_exit_zero", "error", "tests_failed", 0),
    ("pytest_later_usage_error", "error", "test_setup_or_teardown_failed", 0),
    ("multiple_suites", "error", "test_setup_or_teardown_failed", 0),
    ("untracked_result", "error", "test_setup_or_teardown_failed", 0),
    ("no_report", "blocked", "test_report_missing_or_invalid", 0),
    ("truncated", "success", "", 1),
    ("escape", "blocked", "test_script_not_in_workspace", 0),
    ("symlink", "blocked", "test_script_not_in_workspace", 0),
    ("denied", "blocked", "native_test_denied", 0),
    ("source_write", "blocked", "read_only", 0),
])
async def test_native_test_entrypoint_preserves_verification_contract(
    tmp_path, monkeypatch, scenario, status, reason, passed
):
    from aster.config import ExecRule

    (tmp_path / "src").mkdir()
    (tmp_path / "src/subject.py").write_text("ANSWER = 42\n")
    (tmp_path / "tests").mkdir()
    script = tmp_path / "tests/runtests.py"
    script.write_text(
        "import sys, unittest\nfrom subject import ANSWER\n"
        "assert sys.argv[1:] == ['label']\n"
        "class Check(unittest.TestCase):\n"
        "    @unittest.skip('optional')\n"
        "    def test_skip(self): pass\n"
        "    def test_answer(self): self.assertEqual(ANSWER, 42)\n"
        "unittest.TextTestRunner().run(unittest.defaultTestLoader.loadTestsFromTestCase(Check))\n"
    )
    if scenario in {"pytest", "pytest_failure_exit_zero", "pytest_later_usage_error"}:
        (tmp_path / "test_subject.py").write_text(
            "from subject import ANSWER\ndef test_answer(): assert ANSWER == "
            + ("43" if scenario == "pytest_failure_exit_zero" else "42") + "\n"
        )
        script.write_text("import pytest\npytest.main(['-q', 'test_subject.py', '-p', 'no:cacheprovider'])\n")
        if scenario == "pytest_later_usage_error":
            script.write_text(script.read_text() + "pytest.main(['--aster-unknown-option'])\n")
    elif scenario == "spoofed_summaries":
        script = tmp_path / "tests/test"
        script.write_text(
            "print('1 passed in 0.01s')\n"
            "print('Ran 1 test in 0.001s\\n\\nOK')\n"
            "print('tests finished: 2 passed, 1 skipped,\\n3 expected to fail, in 0.12 seconds')\n"
        )
    elif scenario == "skipped":
        script.write_text(script.read_text().replace(
            "    def test_answer", "    @unittest.skip('not implemented')\n    def test_answer"
        ))
    elif scenario == "empty":
        script.write_text("import unittest\nunittest.TextTestRunner().run(unittest.TestSuite())\n")
    elif scenario == "failure_exit_zero":
        script.write_text(script.read_text().replace("self.assertEqual(ANSWER, 42)", "self.fail('bug')"))
    elif scenario == "multiple_suites":
        script.write_text(script.read_text() +
                         "unittest.TextTestRunner().run(unittest.FunctionTestCase(lambda: 1 / 0))\n")
    elif scenario == "untracked_result":
        script.write_text(script.read_text() +
                         "unittest.TextTestRunner(resultclass=unittest.TestResult).run(unittest.TestSuite())\n")
    elif scenario == "no_report":
        script.write_text("print('1 passed')\n")
    elif scenario == "truncated":
        monkeypatch.setattr("aster.safety.runner.OUTPUT_LIMIT", 128)
        script.write_text("print('x' * 200)\n" + script.read_text())
    elif scenario == "source_write":
        script.write_text("from pathlib import Path\nPath('src/subject.py').write_text('changed')\n" + script.read_text())
    command = f"PYTHONPATH=src python {script.relative_to(tmp_path)} label"
    if scenario in {"escape", "symlink"}:
        external = tmp_path.parent / "native_outside.py"
        external.write_text("raise AssertionError('must not run')\n")
        if scenario == "symlink":
            (tmp_path / "linked.py").symlink_to(external)
        command = "python " + ("linked.py" if scenario == "symlink" else "../native_outside.py")
    settings = Settings(approval="auto", memory_enabled=False, read_only=True)
    if scenario == "denied":
        settings.rules = [ExecRule(prefix=["python", "tests/runtests.py"], decision="deny", reason=reason)]
    runtime = Runtime(tmp_path, settings)
    runtime.start(TaskSpec(prompt="Verify through the project test entry point"))
    before = runtime.workspace.snapshot()
    result = await runtime.step(ToolCall(name="run_tests", arguments={"command": command}))
    assert (result.status, result.reason) == (status, reason), result.model_dump()
    assert (runtime.state.verification.state == "passed") == (status == "success")
    assert result.data.get("passed_tests", 0) == passed
    assert runtime.workspace.snapshot() == before and not result.changed_paths


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
            outcome = await runtime.step(ToolCall(name=name, arguments=VISIBILITY["probes"][name]))
            assert (outcome.status, outcome.reason) == ("blocked", reason)
    assert not runtime.workspace.snapshot()


@pytest.mark.smoke
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


async def test_preflight_validates_once_and_direct_registry_still_validates(tmp_path, monkeypatch):
    from aster.schema import ModelReply
    from aster.tools import ReadFile, ToolContext, ToolRegistry

    (tmp_path / "a.txt").write_text("hello")
    validations = []
    original = ReadFile.model_validate

    def validate(value, **kwargs):
        validations.append(value)
        return original(value, **kwargs)

    monkeypatch.setattr(ReadFile, "model_validate", validate)

    class Model:
        async def complete(self, messages, tools):
            if not validations:
                return ModelReply(calls=[ToolCall(name="read_file", arguments={"path": "a.txt"})])
            return ModelReply(text="done")

    runtime = Runtime(tmp_path, Settings(approval="auto", completion="off"), model=Model())
    await runtime.run(TaskSpec(prompt="read"))
    assert len(validations) == 1
    registry = ToolRegistry(ToolContext(runtime.workspace, runtime.sandbox, runtime.state.run_id))
    result = await registry.execute(ToolCall(name="read_file", arguments={}))
    assert result.reason == "invalid_arguments"
    assert len(validations) == 2


async def test_direct_registry_preserves_unknown_tool_diagnostic(tmp_path):
    from aster.tools import ToolContext, ToolRegistry

    runtime = Runtime(tmp_path, Settings(approval="auto", completion="off"))
    runtime.start(TaskSpec(prompt="inspect"))
    registry = ToolRegistry(ToolContext(runtime.workspace, runtime.sandbox, runtime.state.run_id))
    call = ToolCall(name="missing")
    direct = await registry.execute(call)
    assert (direct.status, direct.reason, direct.content) == (
        "blocked", "unknown_tool", "unknown tool missing",
    )
    mediated = await runtime.step(call)
    assert (mediated.status, mediated.reason, mediated.content) == ("blocked", "unknown_tool", "")
