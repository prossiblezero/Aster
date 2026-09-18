"""Node's built-in test runner participates in formal, version-bound verification."""

import shutil

import pytest

from aster.config import Settings
from aster.runtime import Runtime
from aster.schema import TaskSpec, ToolCall

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="Node is unavailable")


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
