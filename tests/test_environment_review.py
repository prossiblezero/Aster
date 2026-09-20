"""Independent regressions for source identity under reused dependencies."""

import shlex

import pytest

from aster.config import Settings
from aster.core.runtime import Runtime
from aster.schema import TaskSpec, ToolCall


@pytest.mark.parametrize(
    "command",
    ["python -m unittest test_example", "pytest -q --import-mode=importlib"],
)
async def test_formal_tests_cannot_substitute_same_named_dependency_for_source(tmp_path, command):
    (tmp_path / "example.py").write_text("VALUE = 99\n")
    (tmp_path / "test_example.py").write_text(
        "import unittest\nimport example\n"
        "class ExampleTest(unittest.TestCase):\n"
        "    def test_value(self): self.assertEqual(example.VALUE, 42)\n"
    )
    runtime = Runtime(
        tmp_path, Settings(approval="auto", reuse_environment=True, memory_enabled=False)
    )
    runtime.start(TaskSpec(prompt="Verify the current project source"))
    install = (
        "import os; from pathlib import Path; p=Path(os.environ['PIP_TARGET']); "
        "p.mkdir(parents=True); (p/'example.py').write_text('VALUE = 42\\n')"
    )
    try:
        result = await runtime.step(
            ToolCall(name="run_shell", arguments={"command": shlex.join(["python", "-c", install])})
        )
        assert result.status == "success", result.model_dump()
        observed = await runtime.step(
            ToolCall(
                name="run_shell",
                arguments={"command": "python -c 'import example; print(example.VALUE)'"},
            )
        )
        assert observed.status == "success" and observed.content.strip() == "99"
        result = await runtime.step(ToolCall(name="run_tests", arguments={"command": command}))
        assert result.status == "error" and result.reason == "tests_failed", result.model_dump()
        assert runtime.state.verification.state != "passed"
        assert "99 != 42" in result.content
    finally:
        runtime.sandbox.close()
