"""Failures identical on the original files are evidence for the agent, then non-blocking."""

import pytest

from aster.config import Settings
from aster.core.runtime import Runtime
from aster.schema import TaskSpec, ToolCall
from aster.tools import testing
from aster.tools.testing import failure_signature

pytestmark = pytest.mark.core

ENV_TEST = "def test_env():\n    raise RuntimeError('cftime version mismatch')\n"
TARGET = "from app import VALUE\n\ndef test_value():\n    assert VALUE == 1\n"


def setup(tmp_path):
    (tmp_path / "app.py").write_text("VALUE = 0\n")
    (tmp_path / "test_env.py").write_text(ENV_TEST)
    (tmp_path / "test_value.py").write_text(TARGET)
    runtime = Runtime(tmp_path, Settings(approval="auto", memory_enabled=False, sandbox="required",
                                         completion="strict"))
    runtime.start(TaskSpec(prompt="Fix VALUE", require_verification=True))
    return runtime


async def run(runtime, command="python -m pytest -q"):
    return await runtime.step(ToolCall(name="run_tests", arguments={"command": command}))


async def edit(runtime, new):
    await runtime.step(ToolCall(name="read_file", arguments={"path": "app.py"}))
    await runtime.step(ToolCall(name="patch_file", arguments={
        "path": "app.py", "old_text": runtime.workspace.read("app.py").strip(), "new_text": new}))


def test_signature_ignores_run_specific_noise():
    a = "E   KeyError: <obj at 0x7f00aa> in /tmp/aster-tests-abc/x.py"
    b = "E   KeyError: <obj at 0x7f11bb> in /tmp/aster-tests-def/x.py"
    assert failure_signature(a) == failure_signature(b)
    assert failure_signature("E   KeyError: 'a'") != failure_signature("E   KeyError: 'b'")


@pytest.mark.asyncio
async def test_unrelated_preexisting_failure_stops_blocking_after_one_completion_check(tmp_path):
    runtime = setup(tmp_path)
    await edit(runtime, "VALUE = 1")
    result = await run(runtime)
    assert result.data["baseline_identical"] == ["pytest: test_env::test_env"]
    assert "your fix is not working yet" in result.content
    runtime.finalize("Fixed VALUE.")
    assert runtime.state.phase != "completed"
    assert "same error on the original files" in runtime.state.feedback
    runtime.finalize("Fixed VALUE; test_env fails the same way on the original files (unverified).")
    assert runtime.state.phase == "completed"
    assert runtime.verifier.final(runtime.state).details["preexisting_failures"] == {
        "pytest: test_env::test_env": "python -m pytest -q"}


@pytest.mark.asyncio
async def test_identical_target_failure_never_completes_without_a_change(tmp_path):
    runtime = setup(tmp_path)
    await run(runtime)  # both tests fail exactly as on the original files
    for _ in range(3):
        runtime.finalize("Nothing to change.")
    assert runtime.state.phase != "completed"


@pytest.mark.asyncio
async def test_a_changed_failure_is_an_ordinary_failure(tmp_path):
    runtime = setup(tmp_path)
    await edit(runtime, "VALUE = 2")
    result = await run(runtime)
    assert "pytest: test_value::test_value" not in result.data["baseline_identical"]
    assert "pytest: test_value::test_value" not in runtime.state.preexisting_failures


@pytest.mark.asyncio
async def test_stale_evidence_is_rechecked_on_the_final_code_without_a_turn(tmp_path):
    runtime = setup(tmp_path)
    await edit(runtime, "VALUE = 1")
    await run(runtime)
    runtime.finalize("Fixed.")  # one rejection naming the pre-existing failure
    await edit(runtime, "VALUE = 1  # comment")
    await runtime.recheck_preexisting()
    runtime.finalize("Fixed; test_env fails as on the original files (unverified).")
    assert runtime.state.phase == "completed"


@pytest.mark.asyncio
async def test_recheck_blocks_when_the_final_code_breaks_the_target_again(tmp_path):
    runtime = setup(tmp_path)
    await edit(runtime, "VALUE = 1")
    await run(runtime)
    runtime.finalize("Fixed.")
    await edit(runtime, "VALUE = 2")
    await runtime.recheck_preexisting()
    runtime.finalize("Fixed again.")
    assert runtime.state.phase != "completed"
    assert "pytest: test_value::test_value" in runtime.state.verification_failures


@pytest.mark.asyncio
async def test_an_edit_after_the_evidence_voids_it_until_rerun(tmp_path):
    runtime = setup(tmp_path)
    await edit(runtime, "VALUE = 1")
    await run(runtime)
    runtime.finalize("Fixed.")
    await edit(runtime, "VALUE = 1  # comment")
    runtime.finalize("Fixed again.")
    assert runtime.state.phase != "completed"  # the identical-failure evidence is stale


@pytest.mark.asyncio
async def test_each_failing_test_is_checked_on_the_original_files_once(tmp_path, monkeypatch):
    runtime = setup(tmp_path)
    calls = []
    original = testing._run_tests

    async def counted(workspace, *args, **kwargs):
        calls.append(workspace.root)
        return await original(workspace, *args, **kwargs)

    monkeypatch.setattr(testing, "_run_tests", counted)
    await run(runtime)
    await run(runtime)
    assert len(calls) == 3  # first run + one baseline run + second run (no new failures)


@pytest.mark.asyncio
async def test_original_files_check_sees_the_real_workspace_path(tmp_path):
    # Editable installs and fixtures address the workspace absolutely; the rerun on the
    # original files must see that path too, or a pre-existing failure looks new.
    (tmp_path / "data.json").write_text('{"v": 1}')
    runtime = setup(tmp_path)
    (tmp_path / "test_env.py").write_text(
        f"import json\n\ndef test_env():\n    json.load(open({str(tmp_path / 'data.json')!r}))\n"
        "    raise RuntimeError('cftime version mismatch')\n")
    runtime._initial_text = runtime.workspace.text_snapshot()
    await edit(runtime, "VALUE = 1")
    result = await run(runtime)
    assert result.data["baseline_identical"] == ["pytest: test_env::test_env"]
