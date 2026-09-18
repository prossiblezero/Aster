"""One real attempt with and without a generic, explicitly discoverable skill package."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.util
import json
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SHARED_PATH = HERE.parent / "stream_comparison/run.py"
SPEC = importlib.util.spec_from_file_location("skills_comparison_shared", SHARED_PATH)
shared = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(shared)


def tree_snapshot(root):
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def experiment_snapshot():
    paths = {
        **{
            name: HERE / name
            for name in ("requirements.md", "checks.py", "test_driver.py", "README.md")
        },
        "run.py": Path(__file__),
        "../stream_comparison/run.py": SHARED_PATH,
    }
    return {
        **{name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in paths.items()},
        **{f"package/{name}": digest for name, digest in tree_snapshot(HERE / "package").items()},
    }


def comparable_launch(previous, current):
    """Exclude random task identity; allow only the documented v1 comparison repair."""
    old_files, new_files = previous["experiment_files"], current["experiment_files"]
    differences = {
        name
        for name in old_files.keys() | new_files.keys()
        if old_files.get(name) != new_files.get(name)
    }
    if (
        differences == {"run.py"}
        and old_files["run.py"] == hashlib.sha256((HERE / "driver-v1.py").read_bytes()).hexdigest()
    ):
        current["driver_amendment"] = {
            "reason": "Ignore random TaskSpec.task_id when comparing task contracts; no model calls occurred in the rejected on preflight.",
            "original_sha256": old_files["run.py"],
            "revised_sha256": new_files["run.py"],
        }
        differences = set()
    contracts = [
        {key: value for key, value in item["task"].items() if key != "task_id"}
        for item in (previous, current)
    ]
    return (
        not differences
        and contracts[0] == contracts[1]
        and all(
            previous[key] == current[key]
            for key in ("configuration", "source", "connection_sha256")
        )
    )


async def grade(support, root):
    previous = support.DATASETS
    support.DATASETS = HERE
    try:
        return await support.external_check(root, "checks.py")
    except Exception as exc:
        return {"passed": False, "status": "grader_error", "error": type(exc).__name__}
    finally:
        support.DATASETS = previous


def skill_metrics(runtime, rows):
    events = [row for row in rows if row["event"] == "tool_result"]
    loads = [row for row in events if row["call"]["name"] == "skill_load"]
    bodies = [row for row in loads if not row["call"]["arguments"].get("resource")]
    resources = [row for row in loads if row["call"]["arguments"].get("resource")]
    direct = [
        row
        for row in events
        if row["call"]["name"] == "read_file"
        and any(
            part in row["call"]["arguments"].get("path", "")
            for part in (".skills/", ".aster/skills/")
        )
    ]
    direct_bodies = [
        row
        for row in direct
        if row["call"]["arguments"]["path"].endswith(("/SKILL.md", "/cli-quality.md"))
    ]
    direct_resources = [row for row in direct if row not in direct_bodies]
    contexts = [row for row in rows if row["event"] == "context_built"]
    first = ""
    if contexts:
        path = runtime.evidence.path(*contexts[0]["request_artifact"].split("/"))
        first = json.dumps(json.loads(path.read_text())["payload"], ensure_ascii=False)
    return {
        "catalog_in_first_request": "cli-quality" in first,
        "body_in_first_request": "Before implementing a standalone Python CLI" in first,
        "resource_in_first_request": "Test the command as a subprocess at least once" in first,
        "body_attempts": len(bodies),
        "body_successes": sum(row["outcome"]["status"] == "success" for row in bodies),
        "resource_attempts": len(resources),
        "resource_successes": sum(row["outcome"]["status"] == "success" for row in resources),
        "direct_body_attempts": len(direct_bodies),
        "direct_body_successes": sum(
            row["outcome"]["status"] == "success" for row in direct_bodies
        ),
        "direct_resource_attempts": len(direct_resources),
        "direct_resource_successes": sum(
            row["outcome"]["status"] == "success" for row in direct_resources
        ),
        "direct_read_events": direct,
        "skill_events": loads,
        "failed_tools": [
            {
                "tool": row["call"]["name"],
                "status": row["outcome"]["status"],
                "reason": row["outcome"]["reason"],
            }
            for row in events
            if row["outcome"]["status"] != "success"
        ],
    }


async def run_condition(args, support, snapshot):
    from aster.config import load_settings
    from aster.runtime import Runtime
    from aster.schema import TaskSpec

    destination = HERE / args.condition
    if destination.exists():
        raise ValueError("Refusing to repeat or overwrite an existing condition")
    root = destination / "candidate"
    root.mkdir(parents=True)
    if args.condition == "on":
        shutil.copytree(HERE / "package", root / ".aster/skills")
    evidence = destination / "evidence"
    settings = load_settings(
        args.env_file,
        model="gpt-5.6-sol",
        protocol="responses",
        stream=False,
        read_parallelism=1,
        reuse_environment=False,
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
        required_artifacts=["eventcount.py", "README.md"],
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
        "connection_sha256": hashlib.sha256(
            json.dumps(
                [
                    settings.base_url.rstrip("/"),
                    settings.api_key.get_secret_value(),
                ]
            ).encode()
        ).hexdigest(),
        "experiment_files": experiment_snapshot(),
    }
    peer = HERE / ("on" if args.condition == "off" else "off") / "evidence/launch.json"
    if peer.exists():
        previous = json.loads(peer.read_text())
        if not comparable_launch(previous, launch):
            raise ValueError("comparison conditions differ beyond skill package availability")
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
    started = time.monotonic()
    summary = {
        "condition": args.condition,
        "source_commit": args.source_commit,
        "operator_feedback": 0,
        "operator_repairs": 0,
    }
    try:
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
        shared.write_json(evidence / "runtime-result.json", summary)
        events = runtime.evidence.events(state.run_id) if state else []
        try:
            summary["skills"] = skill_metrics(runtime, events)
        except (OSError, KeyError, ValueError) as exc:
            summary["skills"] = {"measurement_error": type(exc).__name__}
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
            expected = tree_snapshot(HERE / "package") if args.condition == "on" else {}
            summary["skill_package_unchanged"] = tree_snapshot(root / ".aster/skills") == expected
        except OSError:
            summary["experiment_unchanged"] = summary["skill_package_unchanged"] = False
        summary["usage_metrics"] = support.usage_metrics(summary["usage"], len(model.requests))
        shared.write_json(destination / "result.json", summary)
    print(
        json.dumps(
            {
                key: summary[key]
                for key in (
                    "condition",
                    "phase",
                    "steps",
                    "turns",
                    "seconds",
                    "source_unchanged",
                )
            }
        ),
        flush=True,
    )
    return (
        0
        if summary["phase"] == "completed"
        and independent_grade["passed"]
        and all(
            summary[key]
            for key in ("source_unchanged", "experiment_unchanged", "skill_package_unchanged")
        )
        else 1
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-path", type=Path, required=True)
    parser.add_argument("--describe-source", action="store_true")
    parser.add_argument("--source-digest")
    parser.add_argument("--source-commit")
    parser.add_argument("--condition", choices=["off", "on"])
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
