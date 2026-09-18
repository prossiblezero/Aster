"""Two fresh Runtime tasks per condition; only persistent Memory enablement differs."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.util
import json
import shutil
import sys
import time
from copy import deepcopy
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE_COMMIT = "d20c38fface3751fd366e303e77c81d6ce66c6f8"
WALL_SECONDS = 1800


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def files_snapshot(root):
    return {
        str(path.relative_to(root)): sha256(path.read_bytes())
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def experiment_snapshot():
    return {
        **{
            name: sha256((HERE / name).read_bytes())
            for name in (
                "run.py",
                "test_driver.py",
                "README.md",
                "phase1.md",
                "phase2.md",
                "checks_phase1.py",
                "checks_phase2.py",
            )
        },
        **{f"seed/{name}": digest for name, digest in files_snapshot(HERE / "seed").items()},
    }


def load_helper(source):
    path = source / "exam_project/stream_comparison/run.py"
    spec = importlib.util.spec_from_file_location("frozen_stream_driver", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def grade(support, root, phase):
    previous = support.DATASETS
    support.DATASETS = HERE
    try:
        return await support.external_check(root, f"checks_phase{phase}.py")
    except Exception as exc:
        return {"passed": False, "status": "grader_error", "error": type(exc).__name__}
    finally:
        support.DATASETS = previous


def memory_metrics(runtime, before, events):
    after = deepcopy(runtime.memory.state)
    tools = [event for event in events if event["event"] == "tool_result"]
    stores = [event for event in tools if event["call"]["name"] == "memory_store"]
    discoveries = [
        event
        for event in tools
        if (
            event["call"]["name"] == "tool_load"
            and "memory_store" in event["call"]["arguments"].get("names", [])
        )
    ]
    contexts = [event for event in events if event["event"] == "context_built"]
    first = ""
    if contexts:
        path = runtime.evidence.path(*contexts[0]["request_artifact"].split("/"))
        first = "\n".join(
            item["content"]
            for item in json.loads(path.read_text())["payload"]["input"]
            if isinstance(item.get("content"), str)
        )
    return {
        "before": before,
        "after": after,
        "store_attempts": len(stores),
        "store_successes": sum(event["outcome"]["status"] == "success" for event in stores),
        "discover_attempts": len(discoveries),
        "discover_successes": sum(event["outcome"]["status"] == "success" for event in discoveries),
        "first_request_recalled_ids": [
            lesson["id"] for lesson in before.get("lessons", []) if lesson["text"] in first
        ],
        "run_recalled_ids": list(runtime.state.recalled_ids),
        "memory_tool_events": [
            event
            for event in tools
            if (event["call"]["name"].startswith("memory_") or event in discoveries)
        ],
        "project_read_events": [
            event
            for event in tools
            if (
                event["call"]["name"] == "read_file"
                and event["call"]["arguments"].get("path", "").removeprefix("./") == "PROJECT.md"
            )
        ],
    }


async def run_phase(args, helper, support, source_snapshot):
    from aster.config import load_settings
    from aster.runtime import Runtime
    from aster.schema import TaskSpec

    destination = HERE / args.condition
    root = destination / "candidate"
    result_path = destination / f"phase{args.phase}.json"
    evidence = destination / "evidence" / f"phase{args.phase}"
    if result_path.exists() or evidence.exists():
        raise ValueError("Refusing to repeat a task or overwrite existing evidence")
    if args.phase == 1:
        if root.exists():
            raise ValueError("phase 1 must start with a new candidate")
        shutil.copytree(HERE / "seed", root)
    elif not (destination / "phase1.json").exists():
        raise ValueError("phase 2 requires the recorded first task")
    settings = load_settings(
        args.env_file,
        model="gpt-5.6-sol",
        protocol="responses",
        stream=False,
        context_tokens=128000,
        output_tokens=32768,
        request_timeout=300,
        sandbox="required",
        approval="auto",
        network=False,
        memory_enabled=args.condition == "on",
        memory_evolution=False,
        auto_dream=False,
        max_workers=0,
        read_parallelism=1,
    )
    task = TaskSpec(
        prompt=(HERE / f"phase{args.phase}.md").read_text(),
        max_steps=30,
        max_turns=16,
        require_observation=True,
        require_edit=True,
        require_verification=True,
        required_artifacts=["notetally.py", "README.md"],
    )
    launch = {
        "condition": args.condition,
        "phase": args.phase,
        "source_commit": args.source_commit,
        "source": source_snapshot,
        "source_path": str(args.source_path),
        "python": sys.executable,
        "configuration": settings.identity(),
        "task": task.model_dump(),
        "experiment_files": experiment_snapshot(),
        "seed_files": files_snapshot(HERE / "seed"),
        "connection_sha256": sha256(
            json.dumps(
                [
                    settings.base_url.rstrip("/"),
                    settings.api_key.get_secret_value(),
                ]
            ).encode()
        ),
        "attempts": 1,
        "wall_seconds_limit": WALL_SECONDS,
        "operator_feedback": 0,
        "operator_candidate_repairs": 0,
    }
    for other in ("off", "on"):
        peer = HERE / other / "evidence" / f"phase{args.phase}" / "launch.json"
        if peer.exists():
            previous = json.loads(peer.read_text())
            configurations = [
                {
                    key: value
                    for key, value in item["configuration"].items()
                    if key != "memory_enabled"
                }
                for item in (previous, launch)
            ]
            if (
                configurations[0] != configurations[1]
                or any(
                    previous[key] != launch[key]
                    for key in ("source", "experiment_files", "seed_files", "connection_sha256")
                )
                or previous["task"]["prompt"] != task.prompt
            ):
                raise ValueError("conditions differ beyond persistent Memory")
    helper.write_json(evidence / "launch.json", launch)
    before_grade = await grade(support, root, args.phase)
    helper.write_json(evidence / "before-grade.json", before_grade)
    if before_grade.get("tests", {}).get("executed") != 7 + (args.phase == 2):
        helper.write_json(result_path, {"status": "invalid_grader", "before_grade": before_grade})
        return 1
    if args.phase == 1 and before_grade["passed"]:
        helper.write_json(
            result_path, {"status": "invalid_empty_control", "before_grade": before_grade}
        )
        return 1
    model = helper.timed_model_class()(settings)
    runtime = Runtime(root, settings, model=model, approval_callback=lambda *_: True)
    if runtime.skills.list():
        raise ValueError("Skills must be absent")
    before_memory = deepcopy(runtime.memory.state)
    summary = {
        "condition": args.condition,
        "phase_number": args.phase,
        "source_commit": args.source_commit,
        "before_grade": before_grade,
        "operator_feedback": 0,
        "operator_candidate_repairs": 0,
    }
    started = time.monotonic()
    try:
        await asyncio.wait_for(runtime.run(task), WALL_SECONDS)
    except (Exception, asyncio.CancelledError) as exc:
        summary["error"] = type(exc).__name__
    finally:
        state = runtime.state
        summary.update(
            run_id=state.run_id if state else None,
            session_id=state.session_id if state else None,
            phase=state.phase if state else "not_started",
            stop_reason=state.stop_reason if state else "startup_error",
            steps=state.step if state else 0,
            turns=state.turn if state else 0,
            usage=dict(state.usage) if state else {},
            seconds=time.monotonic() - started,
            final=state.final if state else "",
            requests=model.requests,
        )
        if state:
            events = runtime.evidence.events(state.run_id)
            summary["memory"] = memory_metrics(runtime, before_memory, events)
            summary["metrics"] = support.metrics(
                runtime, summary["seconds"], model_requests=len(model.requests)
            )
            support.archive(runtime, evidence)
        helper.write_json(evidence / "runtime-result.json", summary)
        summary["independent_grade"] = await grade(support, root, args.phase)
        summary["source_unchanged"] = helper.source_snapshot(args.source_path) == source_snapshot
        summary["experiment_unchanged"] = experiment_snapshot() == launch["experiment_files"]
        summary["seed_unchanged"] = all(
            (root / path).is_file() and sha256((root / path).read_bytes()) == digest
            for path, digest in launch["seed_files"].items()
        )
        summary["default_answer_matches"] = args.phase != 2 or "inbox_notes" in summary["final"]
        helper.write_json(result_path, summary)
    print(
        json.dumps(
            {
                key: summary[key]
                for key in (
                    "condition",
                    "phase_number",
                    "phase",
                    "stop_reason",
                    "steps",
                    "turns",
                    "seconds",
                    "source_unchanged",
                )
            }
        ),
        flush=True,
    )
    return int(
        not (
            summary["phase"] == "completed"
            and summary["independent_grade"]["passed"]
            and summary["source_unchanged"]
            and summary["experiment_unchanged"]
            and summary["seed_unchanged"]
        )
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-path", type=Path, required=True)
    parser.add_argument("--source-commit", default=SOURCE_COMMIT)
    parser.add_argument("--source-digest")
    parser.add_argument("--describe-source", action="store_true")
    parser.add_argument("--condition", choices=("off", "on"))
    parser.add_argument("--phase", type=int, choices=(1, 2))
    parser.add_argument("--env-file", type=Path, default=HERE.parents[1] / ".env")
    args = parser.parse_args()
    args.source_path = args.source_path.resolve()
    if args.source_commit != SOURCE_COMMIT:
        parser.error("this experiment is frozen to the stated Memory revision")
    helper = load_helper(args.source_path)
    if args.describe_source:
        print(json.dumps(helper.source_snapshot(args.source_path)))
        return 0
    if not args.source_digest or not args.condition or not args.phase:
        parser.error("running requires source-digest, condition and phase")
    support, snapshot = helper.frozen_imports(args.source_path, args.source_digest)
    return asyncio.run(run_phase(args, helper, support, snapshot))


if __name__ == "__main__":
    raise SystemExit(main())
