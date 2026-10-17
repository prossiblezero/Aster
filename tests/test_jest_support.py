"""Jest projects get formal, per-case verification like pytest and node:test."""

import json
from pathlib import Path

import pytest

from aster.config import Settings
from aster.core.runtime import Runtime
from aster.schema import TaskSpec, ToolCall
from aster.tools.testing import jest_entry, parse_jest

pytestmark = pytest.mark.core


def test_jest_entry_points():
    assert jest_entry(["jest", "--runInBand"]) == 1
    assert jest_entry(["npx", "jest"]) == 2
    assert jest_entry(["/opt/node/bin/node", "/opt/x/node_modules/jest/bin/jest.js", "-i"]) == 2
    assert jest_entry(["node", "jest.js"]) is None  # a workspace script merely named jest.js
    assert jest_entry(["node", "--test"]) is None


def test_parse_jest_cases_and_suite_failures():
    root = Path("/tmp/copy")
    report = {"testResults": [
        {"name": "/tmp/copy/a.spec.js", "status": "failed", "assertionResults": [
            {"fullName": "Robot has a name", "status": "passed", "failureMessages": []},
            {"fullName": "Robot is unique", "status": "failed", "failureMessages": ["Expected 2"]},
            {"title": "later", "ancestorTitles": ["Robot"], "status": "pending"}]},
        {"name": "/tmp/copy/b.spec.js", "status": "failed", "message": "SyntaxError: x", "assertionResults": []},
    ]}
    cases = parse_jest(json.dumps(report), root)
    assert [(c["classname"], c["name"], c["status"]) for c in cases] == [
        ("a.spec.js", "Robot has a name", "passed"), ("a.spec.js", "Robot is unique", "failed"),
        ("a.spec.js", "Robot later", "skipped"), ("b.spec.js", "b.spec.js", "error")]
    assert cases[1]["detail"] == "Expected 2" and "SyntaxError" in cases[3]["detail"]
    with pytest.raises(ValueError):
        parse_jest("[]")


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["jest --json", "jest --outputFile=x.json", "jest --reporters=default"])
async def test_jest_reporter_flags_are_reserved(tmp_path, command):
    runtime = Runtime(tmp_path, Settings(approval="auto", memory_enabled=False, sandbox="required"))
    runtime.start(TaskSpec(prompt="verify"))
    result = await runtime.step(ToolCall(name="run_tests", arguments={"command": command}))
    assert result.reason == "test_reporter_reserved"


@pytest.mark.asyncio
async def test_leading_cd_to_the_workspace_is_dropped(tmp_path):
    root = tmp_path.resolve()
    (root / "test_ok.py").write_text("def test_ok():\n    assert True\n")
    runtime = Runtime(root, Settings(approval="auto", memory_enabled=False, sandbox="required"),
                      approval_callback=lambda *_: True)
    runtime.start(TaskSpec(prompt="verify", require_verification=True))
    result = await runtime.step(ToolCall(name="run_tests", arguments={
        "command": f"cd {root} && python -m pytest -q test_ok.py"}))
    assert result.status == "success", result
    blocked = await runtime.step(ToolCall(name="run_tests", arguments={
        "command": "cd elsewhere && python -m pytest -q"}))
    assert blocked.reason == "test_command_not_supported"
