"""Baseline failures require explicit declarations; changed/new failures stay blocking."""

import pytest

from aster.config import Settings
from aster.context.prompt import workflow_state
from aster.core.recovery import completion_feedback
from aster.core.runtime import Runtime
from aster.schema import Outcome, TaskSpec, ToolCall
from aster.storage.workspace import Workspace
from aster.tools import testing

pytestmark = pytest.mark.core
TEST_ID = "pytest: test_app::test_regression"


def start(root, value=0):
    (root / "app.py").write_text(f"VALUE = {value}\n")
    (root / "test_app.py").write_text(
        "from app import VALUE\ndef test_regression(): assert VALUE == 1\n"
    )
    runtime = Runtime(root, Settings(approval="auto", memory_enabled=False))
    runtime.start(TaskSpec(prompt="Edit unrelated notes and verify", require_verification=True))
    return runtime


async def check(runtime, **arguments):
    return await runtime.step(ToolCall(name="run_tests", arguments={"command": "pytest -q", **arguments}))


async def test_baseline_failure_requires_declaration_and_survives_resume(tmp_path):
    runtime = start(tmp_path)
    await runtime.step(ToolCall(name="write_file", arguments={"path": "notes.txt", "content": "unrelated"}))
    failed = await check(runtime)
    assert failed.reason == "tests_failed"
    assert runtime.state.baseline_results == {TEST_ID: "failed"}
    assert TEST_ID in runtime.state.verification_failures
    assert not runtime.state.preexisting_failures
    assert completion_feedback(runtime.state)
    assert "1 of 1 failing tests also fail there" in failed.content
    declared = await check(runtime, preexisting=[TEST_ID])
    assert declared.reason == "tests_failed"  # Never claim that the underlying test passed.
    assert declared.data["accepted_preexisting"] == [TEST_ID]
    assert 'Accepted as pre-existing: [' in declared.content
    assert not completion_feedback(runtime.state)
    assert runtime.verifier.final(runtime.state).details["preexisting_failures"] == {TEST_ID: "pytest -q"}
    assert workflow_state(runtime.state)["preexisting_failure_count"] == 1
    restored = Runtime(tmp_path, runtime.settings)
    restored.resume(runtime.state.run_id)
    assert restored.state.baseline_results == {TEST_ID: "failed"}
    assert restored.state.preexisting_failures == {TEST_ID: "pytest -q"}
    await check(restored)
    assert not restored.state.verification_failures
    assert restored.verifier.final(restored.state).passed
    (tmp_path / "app.py").write_text("VALUE = 1\n")
    assert (await check(restored)).status == "success"
    assert not restored.state.preexisting_failures
    assert "preexisting_failures" not in workflow_state(restored.state)


async def test_introduced_failure_cannot_be_declared_preexisting(tmp_path):
    runtime = start(tmp_path, value=1)
    await runtime.step(ToolCall(name="read_file", arguments={"path": "app.py"}))
    await runtime.step(ToolCall(name="patch_file", arguments={"path": "app.py", "old_text": "1", "new_text": "0"}))
    failed = await check(runtime, preexisting=[TEST_ID])
    assert runtime.state.baseline_results == {TEST_ID: "passed"}
    assert failed.data["rejected_preexisting"] == [TEST_ID]
    assert "none of the failing tests fail on the unmodified task files" in failed.content
    assert "Not accepted (no baseline failure on record)" in failed.content
    assert not runtime.state.preexisting_failures
    assert runtime.verifier.final(runtime.state).reason == "verification_unresolved_failures"
    assert completion_feedback(runtime.state)


async def test_added_failing_test_is_missing_on_baseline(tmp_path):
    runtime = start(tmp_path, value=1)
    (tmp_path / "test_new.py").write_text("def test_new(): assert False\n")
    name = "pytest: test_new::test_new"
    failed = await check(runtime, preexisting=[name])
    assert runtime.state.baseline_results == {name: "missing"}
    assert failed.data["rejected_preexisting"] == [name]
    assert not runtime.verifier.final(runtime.state).passed


async def test_baseline_copy_restores_changed_deleted_and_added_text_only_once(tmp_path, monkeypatch):
    (tmp_path / "old.txt").write_text("old")
    (tmp_path / "deleted.txt").write_text("restore")
    (tmp_path / "binary.dat").write_bytes(b"\xff\x00")
    workspace = Workspace(tmp_path)
    baseline = workspace.text_snapshot()
    (tmp_path / "old.txt").write_text("changed")
    (tmp_path / "deleted.txt").unlink()
    (tmp_path / "added.txt").write_text("remove")
    roots = []

    async def execute(ws, sandbox, command, timeout, **kwargs):
        roots.append(ws.root)
        assert command == "pytest -q" and timeout == 17
        if ws.root != tmp_path:
            assert ws.read("old.txt") == "old" and ws.read("deleted.txt") == "restore"
            assert not ws.path("added.txt").exists()
            assert ws.path("binary.dat").read_bytes() == b"\xff\x00"
        return Outcome(status="error", reason="tests_failed", data={"test_results": {TEST_ID: "failed"}})

    monkeypatch.setattr(testing, "_run_tests", execute)
    first = await testing.run_tests(workspace, None, "pytest -q", 17, baseline=baseline, checked=set())
    second = await testing.run_tests(workspace, None, "pytest -q", 17, baseline=baseline,
                                     checked=set(first.data["baseline_results"]))
    assert first.data["baseline_results"] == {TEST_ID: "failed"}
    assert "baseline_results" not in second.data
    assert len(roots) == 3 and roots[0] == roots[2] == tmp_path
    assert workspace.read("old.txt") == "changed" and workspace.read("added.txt") == "remove"
    assert not workspace.path("deleted.txt").exists()


async def test_inline_code_skips_baseline(tmp_path, monkeypatch):
    calls = []

    async def execute(*args, **kwargs):
        calls.append(kwargs["code"])
        return Outcome(status="error", reason="tests_failed", data={"test_results": {TEST_ID: "failed"}})

    monkeypatch.setattr(testing, "_run_tests", execute)
    result = await testing.run_tests(Workspace(tmp_path), None, "pytest -q", 1,
                                    code="def test_bad(): assert False", baseline={}, checked=set())
    assert calls == ["def test_bad(): assert False"]
    assert "baseline_results" not in result.data


@pytest.mark.parametrize("status,reason", [("timeout", "test_timeout"), ("error", "test_collection_failed")])
async def test_baseline_unavailable_never_provides_evidence_even_when_output_clipped(tmp_path, monkeypatch, status, reason):
    runtime = start(tmp_path)

    async def execute(workspace, *args, **kwargs):
        if workspace.root == tmp_path:
            return Outcome(status="error", reason="tests_failed", content="output" * 2000,
                           data={"test_results": {TEST_ID: "failed"}})
        return Outcome(status=status, reason=reason)

    monkeypatch.setattr(testing, "_run_tests", execute)
    result = await check(runtime, preexisting=[TEST_ID])
    assert "Baseline check unavailable: " + reason in result.content
    assert not runtime.state.baseline_results and not runtime.state.preexisting_failures
    assert not runtime.verifier.final(runtime.state).passed


def test_declared_failure_does_not_mask_runner_error_and_new_task_needs_new_evidence(tmp_path):
    runtime = start(tmp_path)
    state = runtime.state
    state.baseline_results[TEST_ID] = "failed"
    state.verification_failures[TEST_ID] = "pytest -q"
    result = Outcome(status="error", reason="test_setup_or_teardown_failed", data={
        "is_verification": True, "command": "pytest -q", "preexisting": [TEST_ID],
        "test_results": {TEST_ID: "failed"},
    })
    runtime.verifier.observe(state, result)
    assert not runtime.verifier.final(state).passed
    runtime.finish("cancelled")
    continued = runtime.continue_task("Now fix that test")
    assert not continued.baseline_results and not continued.preexisting_failures
    assert TEST_ID in continued.verification_failures


async def test_declared_failure_cannot_verify_a_workspace_changed_during_tests(tmp_path, monkeypatch):
    runtime = start(tmp_path)
    runtime.state.baseline_results[TEST_ID] = "failed"

    async def execute(workspace, sandbox, *args, **kwargs):
        fingerprint = workspace.fingerprint()
        (tmp_path / "app.py").write_text("VALUE = 2\n")
        return Outcome(status="error", reason="tests_failed", data={
            "test_results": {TEST_ID: "failed"}, "tested_workspace_hash": fingerprint,
            "tested_environment_generation": sandbox.environment_generation,
        })

    monkeypatch.setattr(testing, "_run_tests", execute)
    result = await check(runtime, preexisting=[TEST_ID])
    assert result.reason == "workspace_changed_during_verification"
    assert not runtime.verifier.final(runtime.state).passed
