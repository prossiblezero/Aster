import shlex

from aster.config import Settings
from aster.runtime import Runtime
from aster.schema import TaskSpec, ToolCall


async def test_dependency_change_invalidates_tests_and_continuation_keeps_environment(tmp_path):
    (tmp_path / "test_dependency.py").write_text(
        "def test_dependency():\n    import example\n    assert example.value == 7\n"
    )
    settings = Settings(approval="auto", reuse_environment=True, memory_enabled=False)
    runtime = Runtime(tmp_path, settings)
    runtime.start(TaskSpec(prompt="Inspect and test", require_verification=True))
    install = "import os; from pathlib import Path; p=Path(os.environ['PIP_TARGET']); p.mkdir(parents=True, exist_ok=True); (p/'example.py').write_text('value = 7')"
    outcome = await runtime.step(
        ToolCall(
            name="run_shell",
            arguments={
                "command": shlex.join(["python", "-c", install]),
            },
        )
    )
    assert outcome.status == "success", outcome
    tested = await runtime.step(ToolCall(name="run_tests"))
    assert tested.status == "success", tested
    assert runtime.state.verification.state == "passed"
    runtime.finish("cancelled")
    continued = runtime.continue_task()
    assert continued.verification.state == "passed"
    outcome = await runtime.step(
        ToolCall(
            name="run_shell",
            arguments={"command": "python -c 'import example; print(example.value)'"},
        )
    )
    assert outcome.status == "success" and "7" in outcome.content
    assert runtime.state.verification.state == "missing"
    assert (await runtime.step(ToolCall(name="run_tests"))).status == "success"
    runtime.finish("cancelled")
    resumed = Runtime(tmp_path, settings)
    assert resumed.resume(runtime.state.run_id).verification.state == "missing"
    runtime.new_session()
    runtime.start(TaskSpec(prompt="Fresh task"))
    assert (await runtime.step(ToolCall(name="run_tests"))).status != "success"
    runtime.new_session()


async def test_dropping_environment_before_final_cannot_reuse_verification(tmp_path):
    (tmp_path / "test_simple.py").write_text("def test_ok(): assert True\n")
    runtime = Runtime(
        tmp_path, Settings(approval="auto", reuse_environment=True, memory_enabled=False)
    )
    runtime.start(TaskSpec(prompt="Verify", require_verification=True))
    await runtime.step(ToolCall(name="run_shell", arguments={"command": "python -c 'print(1)'"}))
    assert (await runtime.step(ToolCall(name="run_tests"))).status == "success"
    runtime.sandbox.close()
    assert runtime.finalize("done").phase != "completed"
    assert runtime.state.verification.state == "missing"
