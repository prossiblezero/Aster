"""One-call temporary tests preserve real implementation checks and isolation."""

import pytest

from aster.config import Settings
from aster.core.runtime import Runtime
from aster.schema import TaskSpec, ToolCall


@pytest.mark.parametrize("command,code,status,reason", [
    ("pytest -q", "from subject import answer\ndef test_answer(): assert answer() == 42\n", "success", ""),
    ("python -m pytest -q", "from subject import answer\ndef test_answer(): assert answer() == 43\n", "error", "tests_failed"),
    ("python -m unittest -q", "import unittest\nfrom subject import answer\nclass Checks(unittest.TestCase):\n    def test_answer(self): self.assertEqual(answer(), 42)\n", "success", ""),
    ("pytest -q", "print('10 tests passed')\n", "error", "no_tests_collected"),
    ("pytest -q", "import pytest\n@pytest.mark.skip\ndef test_skipped(): pass\n", "blocked", "no_tests_executed"),
    ("pytest -q", "from pathlib import Path\ndef test_modify_source():\n    Path('subject.py').write_text('tampered')\n", "blocked", "read_only"),
    ("python -m unittest discover", "pass", "blocked", "inline_test_command_not_supported"),
])
async def test_inline_checks_execute_against_project_without_publishing_files(tmp_path, command, code, status, reason):
    (tmp_path / "subject.py").write_text("def answer(): return 42\n")
    runtime = Runtime(tmp_path, Settings(approval="auto", memory_enabled=False))
    runtime.start(TaskSpec(prompt="Check subject.py"))
    before = runtime.workspace.snapshot()
    outcome = await runtime.step(ToolCall(name="run_tests", arguments={"command": command, "code": code}))
    assert (outcome.status, outcome.reason) == (status, reason), outcome.content
    assert (runtime.state.verification.state == "passed") == (status == "success")
    assert runtime.workspace.snapshot() == before and not outcome.changed_paths
    if status == "success":
        assert outcome.data["passed_tests"] == 1
        assert outcome.data["tested_workspace_hash"] == runtime.workspace.fingerprint()
    runtime.sandbox.close()


@pytest.mark.core
async def test_inline_failure_requires_the_same_case_to_pass_after_repair(tmp_path):
    (tmp_path / "subject.py").write_text("def answer(): return 41\n")
    runtime = Runtime(tmp_path, Settings(approval="auto", memory_enabled=False))
    runtime.start(TaskSpec(prompt="Verify", require_verification=True))
    code = "from subject import answer\ndef test_answer(): assert answer() == 42\n"
    failed = await runtime.step(ToolCall(name="run_tests", arguments={
        "command": "pytest -q", "code": code,
    }))
    assert failed.reason == "tests_failed"
    unrelated = await runtime.step(ToolCall(name="run_tests", arguments={
        "command": "pytest -q", "code": "def test_other(): assert True\n",
    }))
    assert unrelated.status == "success"
    assert not runtime.verifier.final(runtime.state).passed
    (tmp_path / "subject.py").write_text("def answer(): return 42\n")
    repaired = await runtime.step(ToolCall(name="run_tests", arguments={
        "command": "pytest -q", "code": code,
    }))
    assert repaired.status == "success"
    assert runtime.verifier.final(runtime.state).passed
    runtime.sandbox.close()
