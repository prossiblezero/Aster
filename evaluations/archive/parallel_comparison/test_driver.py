"""Offline measurements and grader checks; no model and no candidate implementation."""

import importlib.util
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import benchmark_support

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("parallel_comparison_driver", HERE / "run.py")
driver = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(driver)


async def test_initial_fixture_only_project_fails_all_checks_overall(tmp_path):
    root = tmp_path / "candidate"
    shutil.copytree(HERE / "fixtures", root / "fixtures")
    before = {path.name: path.read_bytes() for path in (root / "fixtures").iterdir()}
    previous = benchmark_support.DATASETS
    result = await driver.grade(benchmark_support, root)
    assert benchmark_support.DATASETS == previous
    assert result["isolation"] == "landlock+seccomp" and not result["passed"]
    assert result["tests"]["expected"] == result["tests"]["executed"] == 7
    assert result["tests"]["passed"] < 7
    assert {path.name: path.read_bytes() for path in (root / "fixtures").iterdir()} == before
    assert sorted(path.name for path in root.iterdir()) == ["fixtures"]


def test_batch_measurement_distinguishes_opportunities_overlap_and_denials():
    def call(name, call_id):
        return {"name": name, "call_id": call_id}

    def span(name, call_id, start, end):
        return {"name": name, "call_id": call_id, "start_ns": start, "end_ns": end}

    rows = [
        {
            "event": "model_reply",
            "turn": 1,
            "calls": [call("read_file", "a"), call("search", "b"), call("list_files", "c")],
        },
        {"event": "tool_batch_finished", "duration_ms": 4},
        {
            "event": "model_reply",
            "turn": 2,
            "calls": [call("read_file", "d"), call("run_shell", "e")],
        },
        {"event": "tool_batch_finished", "duration_ms": 6},
        {
            "event": "model_reply",
            "turn": 3,
            "calls": [call("read_file", "f"), call("read_file", "g")],
        },
        {
            "event": "permission_decision",
            "call": call("read_file", "g"),
            "decision": "deny",
            "reason": "synthetic_permission",
        },
        {
            "event": "tool_result",
            "call": call("read_file", "g"),
            "outcome": {"status": "blocked", "reason": "synthetic_permission"},
        },
        {"event": "tool_batch_finished", "duration_ms": 2},
    ]
    spans = [
        span("read_file", "a", 0, 4),
        span("search", "b", 1, 3),
        span("list_files", "c", 2, 4),
        span("read_file", "d", 5, 6),
        span("run_shell", "e", 6, 8),
        span("read_file", "f", 9, 10),
    ]
    result = driver.batch_metrics(rows, spans)
    assert result["eligible_read_batches"] == 2 and result["eligible_read_calls"] == 5
    assert result["overlapping_eligible_batches"] == 1 and result["peak_read_execute_overlap"] == 3
    assert result["tool_batch_seconds"] == 0.012 and result["eligible_batch_seconds"] == 0.006
    assert result["batches"][1]["eligible_read_batch"] is False
    assert result["batches"][2]["executed_calls"] == 1
    assert (
        result["permission_denials"]
        == result["blocked_tools"]
        == [{"tool": "read_file", "reason": "synthetic_permission"}]
    )
    assert driver.peak_overlap([span("read_file", "a", 0, 1), span("read_file", "b", 1, 2)]) == 1


def test_zero_eligible_batches_is_reported_as_unmeasured():
    result = driver.batch_metrics(
        [{"event": "model_reply", "turn": 1, "calls": [{"call_id": "one", "name": "run_shell"}]}],
        [],
    )
    assert result["eligible_read_batches"] == result["overlapping_eligible_batches"] == 0
    assert result["peak_read_execute_overlap"] == 0
    assert result["tool_batch_seconds"] is None
    assert result["batch_duration_coverage"] == {"reported": 0, "batches": 1}


async def test_recorder_preserves_actual_tool_result_and_restores_registry(tmp_path):
    from aster.config import Settings
    from aster.safety.runner import SandboxRunner
    from aster.schema import ToolCall
    from aster.tools import ToolContext, ToolRegistry
    from aster.workspace import Workspace

    (tmp_path / "input.txt").write_text("neutral input\n")
    registry = ToolRegistry(ToolContext(Workspace(tmp_path), SandboxRunner(Settings()), "offline"))
    original = ToolRegistry.execute
    recorder = driver.ToolRecorder()
    call = ToolCall(name="read_file", arguments={"path": "input.txt"})
    with recorder.observe():
        outcome = await registry.execute(call)
    assert ToolRegistry.execute is original
    assert outcome.status == "success" and outcome.content == "neutral input"
    assert len(recorder.spans) == 1
    span = recorder.spans[0]
    assert span["call_id"] == call.call_id and span["end_ns"] >= span["start_ns"]
    assert span["status"] == "success" and span["reason"] == ""


async def test_failed_runtime_and_grader_preserve_usage_and_empty_measurements(
    tmp_path, monkeypatch
):
    import aster.runtime

    root = tmp_path / "experiment"
    root.mkdir()
    shutil.copytree(HERE / "fixtures", root / "fixtures")
    for name in ("requirements.md", "checks.py"):
        shutil.copy2(HERE / name, root / name)
    monkeypatch.setattr(driver, "HERE", root)
    source = tmp_path / "frozen"
    (source / "src/aster").mkdir(parents=True)
    (source / "scripts").mkdir()
    (source / "src/aster/runtime.py").write_text("# source fixture only\n")
    (source / "scripts/benchmark_support.py").write_text("# source fixture only\n")
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
        condition="parallel", env_file=env, source_commit="synthetic", source_path=source
    )
    assert await driver.run_condition(args, support, driver.shared.source_snapshot(source)) == 1
    result = json.loads((root / "parallel/result.json").read_text())
    saved = json.loads((root / "parallel/evidence/runtime-result.json").read_text())
    assert result["usage"] == saved["usage"] == {"input_tokens": 42}
    assert (
        result["error"] == "RuntimeError"
        and result["independent_grade"]["status"] == "grader_error"
    )
    assert result["tool_execution"]["eligible_read_batches"] == 0
    assert (
        result["inputs_unchanged"] and result["source_unchanged"] and result["experiment_unchanged"]
    )
    assert sorted(path.name for path in (root / "parallel/candidate").iterdir()) == ["fixtures"]
