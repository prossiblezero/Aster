"""Node's built-in test runner participates in formal, version-bound verification."""

import shutil
from pathlib import Path

import pytest

from aster.config import Settings
from aster.core.runtime import Runtime
from aster.safety.runner import SandboxRunner
from aster.schema import TaskSpec, ToolCall

node_available = pytest.mark.skipif(
    shutil.which("node", path=SandboxRunner.environment(Path("."))["PATH"]) is None,
    reason="Node is unavailable on the sandbox PATH",
)


@node_available
@pytest.mark.parametrize(
    "program,status,reason",
    [
        ("test('actual', () => assert.equal(1 + 1, 2));", "success", ""),
        ("test('wrong', () => assert.equal(1 + 1, 3));", "error", "tests_failed"),
        ("test.skip('skipped', () => {});", "blocked", "no_tests_executed"),
        ("test.todo('unfinished');", "blocked", "no_tests_executed"),
        ("", "blocked", "no_tests_collected"),
        ("throw Error('startup failure');", "error", "tests_failed"),
    ],
)
async def test_node_formal_result_requires_executed_passing_cases(
    tmp_path, program, status, reason
):
    (tmp_path / "example.test.cjs").write_text(
        "const test = require('node:test');\n"
        "const assert = require('node:assert/strict');\n" + program
    )
    runtime = Runtime(tmp_path, Settings(approval="auto", memory_enabled=False))
    runtime.start(TaskSpec(prompt="Verify the example", require_verification=True))
    result = await runtime.step(
        ToolCall(name="run_tests", arguments={"command": "node --test example.test.cjs"})
    )
    assert result.status == status and result.reason == reason, result.model_dump()
    assert (runtime.state.verification.state == "passed") == (status == "success")
    assert result.data["passed_tests"] == (1 if status == "success" else 0)
    assert not list(tmp_path.glob("__aster_result_*"))
    if status == "success":
        (tmp_path / "example.test.cjs").write_text("changed after verification")
        assert not runtime.verifier.final(runtime.state).passed


@node_available
async def test_node_tests_cannot_publish_source_changes_in_read_only_mode(tmp_path):
    (tmp_path / "source.txt").write_text("original")
    (tmp_path / "example.test.cjs").write_text(
        "require('node:test')('mutates source', () => "
        "require('node:fs').writeFileSync('source.txt', 'overwritten'));"
    )
    runtime = Runtime(tmp_path, Settings(approval="auto", memory_enabled=False, read_only=True))
    runtime.start(TaskSpec(prompt="Verify without editing"))
    result = await runtime.step(
        ToolCall(name="run_tests", arguments={"command": "node --test example.test.cjs"})
    )
    assert result.status == "blocked" and result.reason == "read_only"
    assert (tmp_path / "source.txt").read_text() == "original"
    assert not result.changed_paths and runtime.state.verification.state != "passed"


@node_available
async def test_node_cannot_override_the_trusted_report_destination(tmp_path):
    runtime = Runtime(tmp_path, Settings(approval="auto", memory_enabled=False))
    runtime.start(TaskSpec(prompt="Verify"))
    result = await runtime.step(
        ToolCall(
            name="run_tests",
            arguments={"command": "node --test --test-reporter-destination=forged.xml"},
        )
    )
    assert result.status == "blocked" and result.reason == "test_reporter_reserved"


@pytest.mark.parametrize("selection,status,reason", [
    ("Passing", "success", ""), ("Failing", "error", "tests_failed"),
    ("Skipped", "blocked", "no_tests_executed"),
    ("Missing", "blocked", "no_tests_collected"),
])
async def test_go_formal_results_require_executed_tests(tmp_path, selection, status, reason):
    if shutil.which("go", path=SandboxRunner.environment(tmp_path)["PATH"]) is None:
        pytest.skip("Go is unavailable on the sandbox PATH")
    (tmp_path / "go.mod").write_text("module example.com/check\n\ngo 1.20\n")
    (tmp_path / "check_test.go").write_text(
        'package check\nimport "testing"\n'
        'func TestPassing(t *testing.T) {}\n'
        'func TestFailing(t *testing.T) { t.Fatal("failure") }\n'
        'func TestSkipped(t *testing.T) { t.Skip("skip") }\n'
    )
    runtime = Runtime(tmp_path, Settings(approval="auto", memory_enabled=False))
    runtime.start(TaskSpec(prompt="Verify Go", require_verification=True))
    result = await runtime.step(ToolCall(name="run_tests", arguments={
        "command": f"go test -run Test{selection} ./...", "timeout": 120,
    }))
    assert (result.status, result.reason) == (status, reason), result.model_dump()
    assert (runtime.state.verification.state == "passed") == (status == "success")
    assert result.data["passed_tests"] == (1 if status == "success" else 0)
    assert not list(tmp_path.glob("__aster_result_*"))
