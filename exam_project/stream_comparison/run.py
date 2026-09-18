"""One attempt per condition, with frozen imports and observation-only timing hooks."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib
import json
import os
import sys
import time
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
PYTHON = Path("/home/dzh/.conda/envs/aster/bin/python")
WALL_SECONDS = 1800


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".pending")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def source_snapshot(source):
    files = {}
    for path in sorted(source.rglob("*.py")):
        if any(part in {".git", "__pycache__"} for part in path.relative_to(source).parts):
            continue
        if path.is_symlink():
            raise ValueError("frozen source contains a Python symlink")
        files[str(path.relative_to(source))] = hashlib.sha256(path.read_bytes()).hexdigest()
    if "src/aster/runtime.py" not in files or "scripts/benchmark_support.py" not in files:
        raise ValueError(
            "--source-path must contain frozen src/aster and scripts/benchmark_support.py"
        )
    digest = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
    return {"sha256": digest, "python_files": files}


def experiment_snapshot():
    return {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (Path(__file__), HERE / "requirements.md", HERE / "checks.py")
    }


def frozen_imports(source, expected_digest):
    if Path(sys.executable).resolve() != PYTHON.resolve():
        raise ValueError("Use /home/dzh/.conda/envs/aster/bin/python")
    snapshot = source_snapshot(source)
    if snapshot["sha256"] != expected_digest:
        raise ValueError("source hash differs from the operator's frozen digest")
    configured = {
        Path(value).resolve()
        for value in os.environ.get("PYTHONPATH", "").split(os.pathsep)
        if value
    }
    if not {source / "src", source / "scripts"} <= configured:
        raise ValueError("Set PYTHONPATH to frozen src and scripts before starting this process")
    aster = importlib.import_module("aster")
    support = importlib.import_module("benchmark_support")
    if Path(aster.__file__).resolve() != source / "src/aster/__init__.py":
        raise ValueError("aster was imported from a different source tree")
    if Path(support.__file__).resolve() != source / "scripts/benchmark_support.py":
        raise ValueError("benchmark_support was imported from a different source tree")
    # A process must not have preloaded submodules from the editable checkout.
    for name, module in list(sys.modules.items()):
        if name.startswith("aster.") and getattr(module, "__file__", None):
            if not Path(module.__file__).resolve().is_relative_to(source / "src/aster"):
                raise ValueError("mixed Aster module source paths")
    return support, snapshot


def timed_model_class():
    import httpx

    from aster.provider import ModelClient, ProviderError

    class TimedModel(ModelClient):
        def __init__(self, settings):
            super().__init__(settings)
            self.requests = []

        async def complete(self, messages, tools, *, on_event=None):
            started = time.monotonic()
            record = {
                "request": len(self.requests) + 1,
                "stream": self.settings.stream,
                "first_event_seconds": None,
                "first_text_seconds": None,
                "first_event_kind": "parsed_sse_event"
                if self.settings.stream
                else "complete_response",
                "attempts": [],
                "stream_attempts": [],
                "usage": {},
                "status": "incomplete",
            }
            self.requests.append(record)
            stream_starts = {}

            async def request_hook(_request):
                record["attempts"].append({"started_seconds": time.monotonic() - started})

            async def response_hook(response):
                record["attempts"][-1].update(
                    headers_seconds=time.monotonic() - started,
                    status_code=response.status_code,
                )

            def observe(event):
                attempt = event["attempt"]
                if event["type"] == "begin":
                    stream_starts[attempt] = max(
                        0, time.monotonic() - started - event["elapsed_seconds"]
                    )
                if (
                    event["type"] == "text_delta"
                    and event.get("text", "").strip()
                    and record["first_text_seconds"] is None
                ):
                    record["first_text_seconds"] = time.monotonic() - started
                if event["type"] in {"completed", "error"}:
                    timing = {
                        key: event.get(key)
                        for key in (
                            "type",
                            "attempt",
                            "elapsed_seconds",
                            "first_event_seconds",
                            "first_text_seconds",
                            "retrying",
                            "category",
                        )
                    }
                    record["stream_attempts"].append(timing)
                    for name in ("first_event_seconds", "first_text_seconds"):
                        if timing[name] is not None and record[name] is None:
                            record[name] = stream_starts.get(attempt, 0) + timing[name]
                if on_event:
                    on_event(event)

            original_client = httpx.AsyncClient

            class ObservedClient(original_client):
                def __init__(self, *args, **kwargs):
                    hooks = kwargs.pop("event_hooks", {})
                    kwargs["event_hooks"] = {
                        **hooks,
                        "request": [*hooks.get("request", []), request_hook],
                        "response": [*hooks.get("response", []), response_hook],
                    }
                    super().__init__(*args, **kwargs)

            try:
                with patch.object(httpx, "AsyncClient", ObservedClient):
                    reply = await super().complete(messages, tools, on_event=observe)
                record["usage"] = reply.usage
                record["status"] = "completed"
                if not self.settings.stream:
                    record["first_event_seconds"] = time.monotonic() - started
                    if reply.text.strip():
                        record["first_text_seconds"] = record["first_event_seconds"]
                return reply
            except BaseException as exc:
                record["error"] = type(exc).__name__
                if isinstance(exc, ProviderError):
                    record["category"] = exc.details["category"]
                    record["usage"] = exc.details["usage"]
                raise
            finally:
                record["total_seconds"] = time.monotonic() - started
                record["http_attempts"] = len(record["attempts"])
                record["retries"] = max(0, record["http_attempts"] - 1)

    return TimedModel


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
    root.mkdir(parents=True)
    evidence = destination / "evidence"
    settings = load_settings(
        args.env_file,
        model="gpt-5.6-sol",
        protocol="responses",
        stream=args.condition == "stream",
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
        required_artifacts=["linecounts.py", "README.md"],
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
        "wall_seconds_limit": WALL_SECONDS,
        "operator_feedback": 0,
        "operator_repairs": 0,
        "endpoint_sha256": hashlib.sha256(settings.base_url.rstrip("/").encode()).hexdigest(),
        "experiment_files": experiment_snapshot(),
    }
    peer = (
        HERE / ("stream" if args.condition == "nonstream" else "nonstream") / "evidence/launch.json"
    )
    if peer.exists():
        previous = json.loads(peer.read_text())
        previous_configuration = {
            key: value for key, value in previous["configuration"].items() if key != "stream"
        }
        current_configuration = {
            key: value for key, value in launch["configuration"].items() if key != "stream"
        }
        if (
            previous_configuration != current_configuration
            or previous["source"] != snapshot
            or previous["endpoint_sha256"] != launch["endpoint_sha256"]
            or previous["experiment_files"] != launch["experiment_files"]
            or previous["task"]["prompt"] != task.prompt
        ):
            raise ValueError("comparison conditions differ beyond streaming")
    write_json(evidence / "launch.json", launch)
    empty_grade = await grade(support, root)
    write_json(evidence / "empty-grade.json", empty_grade)
    if empty_grade.get("passed") or empty_grade.get("tests", {}).get("executed") != 7:
        write_json(
            destination / "result.json",
            {"status": "invalid_empty_grader", "independent_grade": empty_grade},
        )
        return 1
    model = timed_model_class()(settings)
    runtime = Runtime(root, settings, model=model, approval_callback=lambda *_: True)
    started = time.monotonic()
    summary = {
        "condition": args.condition,
        "source_commit": args.source_commit,
        "operator_feedback": 0,
        "operator_repairs": 0,
    }
    try:
        await asyncio.wait_for(runtime.run(task), WALL_SECONDS)
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
        write_json(evidence / "runtime-result.json", summary)
        independent_grade = await grade(support, root)
        summary["independent_grade"] = independent_grade
        try:
            after = source_snapshot(args.source_path)
            summary["source_unchanged"] = after == snapshot
            summary["source_sha256_after"] = after["sha256"]
        except Exception as exc:
            summary["source_unchanged"] = False
            summary["source_check_error"] = type(exc).__name__
        try:
            summary["experiment_unchanged"] = experiment_snapshot() == launch["experiment_files"]
        except OSError:
            summary["experiment_unchanged"] = False
        summary["usage_metrics"] = support.usage_metrics(summary["usage"], len(model.requests))
        write_json(destination / "result.json", summary)
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
        and summary["source_unchanged"]
        and summary["experiment_unchanged"]
        else 1
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-path", type=Path, required=True)
    parser.add_argument("--describe-source", action="store_true")
    parser.add_argument("--source-digest")
    parser.add_argument("--source-commit")
    parser.add_argument("--condition", choices=["nonstream", "stream"])
    parser.add_argument("--env-file", type=Path, default=HERE.parents[1] / ".env")
    args = parser.parse_args(argv)
    args.source_path = args.source_path.resolve()
    if args.describe_source:
        print(json.dumps(source_snapshot(args.source_path), indent=2))
        return 0
    if not all((args.source_digest, args.source_commit, args.condition)):
        parser.error("running requires --source-digest, --source-commit and --condition")
    support, snapshot = frozen_imports(args.source_path, args.source_digest)
    return asyncio.run(run_condition(args, support, snapshot))


if __name__ == "__main__":
    raise SystemExit(main())
