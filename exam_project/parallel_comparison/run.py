"""One real task per read-parallelism condition; observe execution without delaying it."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.util
import json
import shutil
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
SHARED_PATH = HERE.parent / "stream_comparison/run.py"
SPEC = importlib.util.spec_from_file_location("parallel_comparison_shared", SHARED_PATH)
shared = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(shared)
READ_TOOLS = {"read_file", "search", "list_files"}


def experiment_snapshot():
    paths = {
        "run.py": Path(__file__),
        "requirements.md": HERE / "requirements.md",
        "checks.py": HERE / "checks.py",
        "../stream_comparison/run.py": SHARED_PATH,
        **{f"fixtures/{path.name}": path for path in sorted((HERE / "fixtures").glob("*.json"))},
    }
    return {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in paths.items()}


def peak_overlap(spans):
    events = [(span["start_ns"], 1) for span in spans] + [(span["end_ns"], -1) for span in spans]
    active = peak = 0
    for _stamp, delta in sorted(events):
        active += delta
        peak = max(peak, active)
    return peak


class ToolRecorder:
    """Capture actual execute intervals; no sleeps, barriers, or scheduler changes."""

    def __init__(self):
        self.started_ns = time.monotonic_ns()
        self.spans = []
        self.lock = threading.Lock()

    @contextmanager
    def observe(self):
        from aster.tools import ToolRegistry

        original = ToolRegistry.execute

        async def execute(registry, call):
            start = time.monotonic_ns()
            status = "exception"
            reason = ""
            try:
                outcome = await original(registry, call)
                status, reason = outcome.status, outcome.reason
                return outcome
            except BaseException as exc:
                reason = type(exc).__name__
                raise
            finally:
                end = time.monotonic_ns()
                with self.lock:
                    self.spans.append(
                        {
                            "call_id": call.call_id,
                            "name": call.name,
                            "start_ns": start - self.started_ns,
                            "end_ns": end - self.started_ns,
                            "thread_id": threading.get_ident(),
                            "status": status,
                            "reason": reason,
                        }
                    )

        with patch.object(ToolRegistry, "execute", execute):
            yield


def batch_metrics(rows, spans):
    batches = []
    pending = None
    for row in rows:
        if row["event"] == "model_reply" and row.get("calls"):
            calls = row["calls"]
            pending = {
                "turn": row["turn"],
                "calls": [{"call_id": call["call_id"], "name": call["name"]} for call in calls],
                "eligible_read_batch": len(calls) >= 2
                and all(call["name"] in READ_TOOLS for call in calls),
                "duration_ms": None,
            }
            batches.append(pending)
        elif row["event"] == "tool_batch_finished" and pending is not None:
            pending["duration_ms"] = row["duration_ms"]
            pending = None
    for batch in batches:
        ids = {call["call_id"] for call in batch["calls"]}
        executed = [span for span in spans if span["call_id"] in ids]
        batch["executed_calls"] = len(executed)
        batch["peak_execute_overlap"] = peak_overlap(executed)
        batch["sum_execute_ms"] = sum(
            (span["end_ns"] - span["start_ns"]) / 1e6 for span in executed
        )
    eligible = [batch for batch in batches if batch["eligible_read_batch"]]
    denials = [
        {"tool": row["call"]["name"], "reason": row["reason"]}
        for row in rows
        if row["event"] == "permission_decision" and row["decision"] == "deny"
    ]
    blocked = [
        {"tool": row["call"]["name"], "reason": row["outcome"]["reason"]}
        for row in rows
        if row["event"] == "tool_result" and row["outcome"]["status"] == "blocked"
    ]

    def batch_seconds(items):
        if any(batch["duration_ms"] is None for batch in items):
            return None
        return sum(batch["duration_ms"] for batch in items) / 1000

    return {
        "batches": batches,
        "eligible_read_batches": len(eligible),
        "eligible_read_calls": sum(len(batch["calls"]) for batch in eligible),
        "overlapping_eligible_batches": sum(
            batch["peak_execute_overlap"] >= 2 for batch in eligible
        ),
        "peak_read_execute_overlap": peak_overlap(
            [span for span in spans if span["name"] in READ_TOOLS]
        ),
        "tool_batch_seconds": batch_seconds(batches),
        "eligible_batch_seconds": batch_seconds(eligible),
        "batch_duration_coverage": {
            "reported": sum(batch["duration_ms"] is not None for batch in batches),
            "batches": len(batches),
        },
        "permission_denials": denials,
        "blocked_tools": blocked,
        "spans": sorted(spans, key=lambda span: span["start_ns"]),
    }


async def grade(support, root):
    previous = support.DATASETS
    support.DATASETS = HERE
    try:
        return await support.external_check(root, "checks.py")
    except Exception as exc:
        return {"passed": False, "status": "grader_error", "error": type(exc).__name__}
    finally:
        support.DATASETS = previous


async def run_condition(args, support, snapshot):
    from aster.config import load_settings
    from aster.runtime import Runtime
    from aster.schema import TaskSpec

    destination = HERE / args.condition
    if destination.exists():
        raise ValueError("Refusing to repeat or overwrite an existing condition")
    root = destination / "candidate"
    shutil.copytree(HERE / "fixtures", root / "fixtures")
    evidence = destination / "evidence"
    settings = load_settings(
        args.env_file,
        model="gpt-5.6-sol",
        protocol="responses",
        stream=False,
        read_parallelism=1 if args.condition == "serial" else 3,
        context_tokens=128000,
        output_tokens=32768,
        request_timeout=300,
        sandbox="required",
        approval="auto",
        network=False,
        memory_enabled=False,
        memory_evolution=False,
        auto_dream=False,
        max_workers=0,
    )
    task = TaskSpec(
        prompt=(HERE / "requirements.md").read_text(),
        max_steps=30,
        max_turns=16,
        require_observation=True,
        require_edit=True,
        require_verification=True,
        required_artifacts=["tagmerge.py", "README.md"],
    )
    launch = {
        "source_commit": args.source_commit,
        "source_path": str(args.source_path),
        "source": snapshot,
        "python": sys.executable,
        "configuration": settings.identity(),
        "task": task.model_dump(),
        "condition": args.condition,
        "attempts": 1,
        "wall_seconds_limit": shared.WALL_SECONDS,
        "operator_feedback": 0,
        "operator_repairs": 0,
        "endpoint_sha256": hashlib.sha256(settings.base_url.rstrip("/").encode()).hexdigest(),
        "experiment_files": experiment_snapshot(),
    }
    peer = HERE / ("parallel" if args.condition == "serial" else "serial") / "evidence/launch.json"
    if peer.exists():
        previous = json.loads(peer.read_text())
        old = {
            key: value
            for key, value in previous["configuration"].items()
            if key != "read_parallelism"
        }
        new = {
            key: value
            for key, value in launch["configuration"].items()
            if key != "read_parallelism"
        }
        if old != new or any(
            previous[key] != launch[key]
            for key in ("source", "endpoint_sha256", "experiment_files")
        ):
            raise ValueError("comparison conditions differ beyond read_parallelism")
    shared.write_json(evidence / "launch.json", launch)
    initial = await grade(support, root)
    shared.write_json(evidence / "initial-grade.json", initial)
    if initial.get("passed") or initial.get("tests", {}).get("executed") != 7:
        shared.write_json(
            destination / "result.json",
            {"status": "invalid_initial_grader", "independent_grade": initial},
        )
        return 1
    model = shared.timed_model_class()(settings)
    runtime = Runtime(root, settings, model=model, approval_callback=lambda *_: True)
    recorder = ToolRecorder()
    started = time.monotonic()
    summary = {
        "condition": args.condition,
        "source_commit": args.source_commit,
        "operator_feedback": 0,
        "operator_repairs": 0,
    }
    try:
        with recorder.observe():
            await asyncio.wait_for(runtime.run(task), shared.WALL_SECONDS)
    except (Exception, asyncio.CancelledError) as exc:
        summary["error"] = type(exc).__name__
    finally:
        state = runtime.state
        summary.update(
            run_id=state.run_id if state else None,
            phase=state.phase if state else "not_started",
            stop_reason=state.stop_reason if state else "startup_error",
            steps=state.step if state else 0,
            turns=state.turn if state else 0,
            usage=dict(state.usage) if state else {},
            seconds=time.monotonic() - started,
            requests=model.requests,
        )
        events = runtime.evidence.events(state.run_id) if state else []
        summary["tool_execution"] = batch_metrics(events, recorder.spans)
        shared.write_json(evidence / "runtime-result.json", summary)
        independent_grade = await grade(support, root)
        summary["independent_grade"] = independent_grade
        try:
            after = shared.source_snapshot(args.source_path)
            summary["source_unchanged"] = after == snapshot
            summary["source_sha256_after"] = after["sha256"]
        except Exception as exc:
            summary["source_unchanged"] = False
            summary["source_check_error"] = type(exc).__name__
        try:
            summary["experiment_unchanged"] = experiment_snapshot() == launch["experiment_files"]
        except OSError:
            summary["experiment_unchanged"] = False
        expected_inputs = {
            path.name: path.read_bytes() for path in (HERE / "fixtures").glob("*.json")
        }
        try:
            actual_inputs = {
                path.name: path.read_bytes() for path in (root / "fixtures").glob("*.json")
            }
            summary["inputs_unchanged"] = actual_inputs == expected_inputs
        except OSError as exc:
            summary["inputs_unchanged"] = False
            summary["input_check_error"] = type(exc).__name__
        summary["usage_metrics"] = support.usage_metrics(summary["usage"], len(model.requests))
        shared.write_json(destination / "result.json", summary)
    print(
        json.dumps(
            {
                key: summary[key]
                for key in ("condition", "phase", "steps", "turns", "seconds", "source_unchanged")
            }
        ),
        flush=True,
    )
    return (
        0
        if summary["phase"] == "completed"
        and independent_grade["passed"]
        and all(
            summary[key] for key in ("source_unchanged", "experiment_unchanged", "inputs_unchanged")
        )
        else 1
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-path", type=Path, required=True)
    parser.add_argument("--describe-source", action="store_true")
    parser.add_argument("--source-digest")
    parser.add_argument("--source-commit")
    parser.add_argument("--condition", choices=["serial", "parallel"])
    parser.add_argument("--env-file", type=Path, default=HERE.parents[1] / ".env")
    args = parser.parse_args(argv)
    args.source_path = args.source_path.resolve()
    if args.describe_source:
        print(json.dumps(shared.source_snapshot(args.source_path), indent=2))
        return 0
    if not all((args.source_digest, args.source_commit, args.condition)):
        parser.error("running requires --source-digest, --source-commit and --condition")
    support, snapshot = shared.frozen_imports(args.source_path, args.source_digest)
    return asyncio.run(run_condition(args, support, snapshot))


if __name__ == "__main__":
    raise SystemExit(main())
