"""Offline experiment checks: no model calls or candidate implementations."""

import importlib.util
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import benchmark_support
import pytest

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("skills_comparison_driver", HERE / "run.py")
driver = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(driver)


@pytest.mark.parametrize("condition", ["off", "on"])
async def test_grader_rejects_no_implementation_with_or_without_skill(tmp_path, condition):
    root = tmp_path / "candidate"
    root.mkdir()
    if condition == "on":
        shutil.copytree(HERE / "package", root / ".aster/skills")
    before = driver.tree_snapshot(root)
    previous = benchmark_support.DATASETS
    result = await driver.grade(benchmark_support, root)
    assert benchmark_support.DATASETS == previous
    assert result["isolation"] == "landlock+seccomp" and not result["passed"]
    assert result["tests"]["expected"] == result["tests"]["executed"] == 7
    assert result["tests"]["passed"] < 7
    assert driver.tree_snapshot(root) == before


def test_skill_metrics_distinguish_discovery_body_resource_and_failure(tmp_path):
    request = tmp_path / "request.json"
    request.write_text(json.dumps({"payload": {"skills": "cli-quality: generic CLI quality"}}))
    runtime = SimpleNamespace(evidence=SimpleNamespace(path=lambda *_parts: request))

    def load(resource, status):
        return {
            "event": "tool_result",
            "call": {
                "name": "skill_load",
                "arguments": {"name": "cli-quality", "resource": resource},
            },
            "outcome": {"status": status, "reason": "synthetic" if status != "success" else ""},
        }

    rows = [
        {"event": "context_built", "request_artifact": "request.json"},
        load("", "success"),
        load("references/python-cli.md", "success"),
        load("missing.md", "error"),
        {
            "event": "tool_result",
            "call": {"name": "read_file", "arguments": {"path": ".skills/cli-quality/SKILL.md"}},
            "outcome": {"status": "success", "reason": ""},
        },
    ]
    metrics = driver.skill_metrics(runtime, rows)
    assert metrics["catalog_in_first_request"]
    assert not metrics["body_in_first_request"] and not metrics["resource_in_first_request"]
    assert metrics["body_attempts"] == metrics["body_successes"] == 1
    assert metrics["resource_attempts"] == 2 and metrics["resource_successes"] == 1
    assert len(metrics["failed_tools"]) == 1
    assert metrics["direct_body_attempts"] == metrics["direct_body_successes"] == 1
    assert metrics["direct_resource_attempts"] == 0
    empty = driver.skill_metrics(runtime, [])
    assert not empty["catalog_in_first_request"]
    assert empty["body_successes"] == empty["resource_successes"] == 0


async def test_failed_runtime_and_grader_preserve_usage_without_candidate_repairs(
    tmp_path, monkeypatch
):
    import aster.runtime

    root = tmp_path / "experiment"
    root.mkdir()
    shutil.copytree(HERE / "package", root / "package")
    for name in ("requirements.md", "checks.py", "README.md", "test_driver.py"):
        shutil.copy2(HERE / name, root / name)
    monkeypatch.setattr(driver, "HERE", root)
    source = tmp_path / "frozen"
    (source / "src/aster").mkdir(parents=True)
    (source / "scripts").mkdir()
    (source / "src/aster/runtime.py").write_text("# synthetic source identity fixture\n")
    (source / "scripts/benchmark_support.py").write_text("# synthetic source identity fixture\n")
    env = tmp_path / ".env"
    env.write_text("")
    state = SimpleNamespace(
        run_id="synthetic",
        phase="failed",
        stop_reason="synthetic_error",
        step=0,
        turn=1,
        usage={"input_tokens": 42},
    )

    class FailedRuntime:
        def __init__(self, *_args, **_kwargs):
            self.state = state
            self.evidence = SimpleNamespace(events=lambda run_id: [])

        async def run(self, task):
            raise RuntimeError("synthetic failure")

    count = 0

    async def check(*_args):
        nonlocal count
        count += 1
        if count == 1:
            return {"passed": False, "tests": {"executed": 7}}
        raise OSError("synthetic grader failure")

    support = SimpleNamespace(
        DATASETS=tmp_path,
        external_check=check,
        usage_metrics=lambda usage, requests: {"reported_input_tokens": usage["input_tokens"]},
    )
    monkeypatch.setattr(aster.runtime, "Runtime", FailedRuntime)
    monkeypatch.setattr(
        driver.shared, "timed_model_class", lambda: lambda settings: SimpleNamespace(requests=[])
    )
    args = SimpleNamespace(
        condition="on", env_file=env, source_commit="synthetic", source_path=source
    )
    assert await driver.run_condition(args, support, driver.shared.source_snapshot(source)) == 1
    result = json.loads((root / "on/result.json").read_text())
    saved = json.loads((root / "on/evidence/runtime-result.json").read_text())
    assert result["usage"] == saved["usage"] == {"input_tokens": 42}
    assert result["error"] == "RuntimeError"
    assert result["independent_grade"]["status"] == "grader_error"
    assert result["skills"]["body_attempts"] == 0
    assert all(
        result[key]
        for key in ("source_unchanged", "experiment_unchanged", "skill_package_unchanged")
    )
    assert driver.tree_snapshot(root / "on/candidate/.aster/skills") == driver.tree_snapshot(
        HERE / "package"
    )
    assert sorted(path.name for path in (root / "on/candidate").iterdir()) == [".aster"]
