"""Check measurement integrity, not the agent's benchmark answers."""

import json
from pathlib import Path

import pytest
from benchmark import pass_at_k
from benchmark_support import (
    MemoryPolicy,
    external_check,
    load_bank,
    metrics,
    prepare,
    run_memory,
    run_task,
)

from aster.config import Settings
from aster.runtime import Runtime
from aster.schema import TaskSpec


def test_pass_at_k_counts_failures_and_requires_enough_attempts():
    rows = [{"task_id": "a", "passed": p} for p in [True, False, False, True, False]]
    assert pass_at_k(rows, 1) == pytest.approx(0.4)
    assert pass_at_k(rows, 3) == pytest.approx(0.9)
    assert pass_at_k(rows, 6) is None
    assert pass_at_k([], 1) is None


def test_usage_total_is_not_counted_twice(tmp_path):
    runtime = Runtime(tmp_path, Settings())
    runtime.start(TaskSpec(prompt="inspect"))
    runtime.state.usage = {"input_tokens": 12, "output_tokens": 3, "total_tokens": 15}
    assert metrics(runtime, 0)["total_tokens"] == 15
    runtime.state.usage.pop("total_tokens")
    assert metrics(runtime, 0)["total_tokens"] == 15


@pytest.mark.asyncio
async def test_grader_ignores_public_tests_and_pytest_configuration(tmp_path, monkeypatch):
    task = next(t for t in load_bank() if t["id"] == "humaneval_0")
    root = tmp_path / "candidate"
    prepare(task, root)
    verifier = task["checks"]["verifier"]
    # A candidate cannot pass by emptying its public suite or injecting skip configuration.
    (root / "test_public.py").write_text("")
    (root / "pytest.ini").write_text("[pytest]\naddopts = -k nonexistent\n")
    (root / "conftest.py").write_text("raise RuntimeError('should never load')\n")
    assert not (await external_check(root, verifier))["passed"]
    action = task["actions"][1]["arguments"]
    path = root / action["path"]
    path.write_text(path.read_text().replace(action["old_text"], action["new_text"]))
    monkeypatch.chdir(tmp_path)
    assert (await external_check(Path("candidate"), verifier))["passed"]
    assert not (root / "__evaluation_tests.py").exists()


@pytest.mark.asyncio
async def test_memory_control_uses_observations_in_a_fresh_runtime(tmp_path):
    task = next(t for t in load_bank() if t["id"] == "memory_deploy")
    rows = []
    for enabled in (False, True):
        rows.append(
            await run_memory(
                task,
                tmp_path / str(enabled),
                tmp_path / f"trace-{enabled}",
                Settings(approval="auto"),
                enabled=enabled,
            )
        )
    assert all(row["passed"] and row["checks"]["fresh_followup"] for row in rows)
    assert [row["read_calls"] for row in rows] == [1, 0]
    reply = await MemoryPolicy(task["source_path"]).complete(
        [{"role": "user", "content": "FACT: deploy ring is amber\n"}], []
    )
    assert json.loads(reply.text) == {"value": "amber"}


@pytest.mark.asyncio
async def test_endpoint_grader_matches_prompt_and_candidate_is_archived(tmp_path):
    task = next(t for t in load_bank() if t["id"] == "add-endpoint-with-test")
    # The task does not prescribe a JSON payload. A healthy endpoint is also valid.
    for action in task["actions"]:
        args = action.get("arguments", {})
        for key in ("content", "new_text"):
            if key in args:
                args[key] = args[key].replace('"ok"', '"healthy"').replace("'ok'", "'healthy'")
    result = await run_task(
        task, tmp_path / "workspace", tmp_path / "archive", Settings(approval="auto")
    )
    assert result["passed"]
    assert "healthy" in (tmp_path / "archive" / "candidate" / "app.py").read_text()
    diff = (tmp_path / "archive" / "patch.diff").read_text()
    assert "+def health" in diff
    assert (tmp_path / "archive" / "candidate" / "tests" / "test_app.py").exists()
