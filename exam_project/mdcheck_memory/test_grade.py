"""Negative controls for the external grader; no candidate implementation is supplied."""

import tempfile
from pathlib import Path

import pytest
from grade import RUNS, grade, validate


@pytest.mark.asyncio
@pytest.mark.parametrize("program", ["raise SystemExit(0)\n", 'print(\'{"checked":0,"broken":[]}\')\n'])
async def test_empty_workspace_and_empty_success_cannot_pass(program):
    RUNS.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="grade-control-", dir=RUNS) as directory:
        root = Path(directory)
        empty = await grade(root, 1)
        assert empty["passed"] is False
        assert empty["checks"] and empty["checks"][0]["passed"] is False
        (root / "mdcheck.py").write_text(program)
        (root / "pyproject.toml").write_text('[project]\nname="empty-control"\nversion="0"\n')
        (root / "README.md").write_text("Deliberately empty negative control.\n")
        (root / "test_empty.py").write_text("def test_placeholder(): assert True\n")
        before = set(RUNS.glob("grade-*"))
        result = await grade(root, 1)
        assert result["passed"] is False
        assert len(result["checks"]) > 1
        assert all(not item["passed"] for item in result["checks"][1:])
        assert any("empty stdout" in item["detail"] or "expected=" in item["detail"]
                   for item in result["checks"])
        assert set(RUNS.glob("grade-*")) == before
        assert (root / "mdcheck.py").read_text() == program


def test_json_output_requires_exact_typed_values_and_nonempty_capture():
    case = {"checked": 1, "broken": []}
    assert validate(case, {"stdout": '{"checked":1,"broken":[]}', "stderr": "", "exit_code": 0})[0]
    for value in ("", '{"checked":0,"broken":[]}', '{"checked":true,"broken":[]}',
                  '{"checked":1,"broken":[]}\nextra log', '{"checked":1,"broken":[],"extra":0}'):
        assert not validate(case, {"stdout": value, "stderr": "", "exit_code": 0})[0]
