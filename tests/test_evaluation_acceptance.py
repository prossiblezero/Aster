"""The paid runner's modes exercise real completion behavior without model requests."""
import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from aster.config import Settings
from aster.core.runtime import Runtime
from aster.schema import ModelReply, TaskSpec, ToolCall

REPO = Path(__file__).resolve().parents[1]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["benchmark", "default", "off"])
@pytest.mark.parametrize("ask_tests", [False, True])
async def test_evaluation_modes_enforce_only_their_completion_requirements(tmp_path, mode, ask_tests):
    # Load the actual patcher without importing the runner's Docker/scoring dependencies.
    source = REPO / "evaluations/harness-v12-20261017/evaluate.py"
    function = next(node for node in ast.parse(source.read_text()).body
                    if isinstance(node, ast.FunctionDef) and node.name == "patch_acceptance_driver")
    namespace = {}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), "exec"), namespace)
    original = (REPO / "evaluations/general-coding-50-20261012/aster_episode.py").read_text()
    driver = tmp_path / "driver.py"
    driver.write_text(original)
    namespace["patch_acceptance_driver"](driver, mode)
    tree = ast.parse(driver.read_text())
    if mode == "benchmark":
        assert driver.read_text() == original
    prompt = "Implement the change in app.py" + (" and run tests" if ask_tests else "")
    values = {
        "Settings": Settings, "TaskSpec": TaskSpec,
        "os": SimpleNamespace(environ={"BENCH_BASE_URL": "http://unused.invalid/v1"}),
        "Path": lambda _: SimpleNamespace(read_text=lambda: prompt),
    }
    calls = {node.func.id: node for node in ast.walk(tree) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Name) and node.func.id in {"Settings", "TaskSpec"}}
    settings, task = (eval(compile(ast.Expression(calls[name]), str(driver), "eval"), values)
                      for name in ("Settings", "TaskSpec"))
    assert settings.completion == ("off" if mode == "off" else "strict")
    assert ("require_verification" in task.model_fields_set) == (mode == "benchmark")
    assert settings.sandbox == "required" and task.max_turns == 500
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "app.py").write_text("VALUE = 0\n")

    class Model:
        replies = iter([
            ModelReply(calls=[ToolCall(name="read_file", arguments={"path": "app.py"}, call_id="r")]),
            ModelReply(calls=[ToolCall(name="write_file", arguments={
                "path": "app.py", "content": "VALUE = 1\n"}, call_id="w")]),
            *[ModelReply(text="Implemented.") for _ in range(3)],
        ])

        async def complete(self, messages, tools):
            return next(self.replies)

    settings = settings.model_copy(update={"memory_enabled": False, "background_maintenance": False})
    runtime = Runtime(workspace, settings, model=Model())
    try:
        state = await runtime.run(task)
        assert state.task.require_verification == (mode == "benchmark" or ask_tests)
        expected = "completed" if mode == "off" or (mode == "default" and not ask_tests) else "stopped"
        assert state.phase == expected
        assert state.final_revisions == (2 if expected == "stopped" else 0)
        assert state.verification.state == "missing"
        assert (workspace / "app.py").read_text() == "VALUE = 1\n"
    finally:
        await runtime.maintenance.close()
        await runtime.workers.close()
        runtime.sandbox.close()
