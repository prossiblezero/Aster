"""Run one bounded real Aster task; never implement or repair the candidate here."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

from aster.config import load_settings
from aster.provider import ModelClient
from aster.runtime import Runtime
from aster.schema import TaskSpec

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
PYTHON = Path("/home/dzh/.conda/envs/aster/bin/python")


def digest_files(root, pattern="**/*"):
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.glob(pattern))
        if p.is_file() and not p.is_symlink() and "__pycache__" not in p.parts
    }


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def copy_product(source, target):
    shutil.copytree(
        source,
        target,
        ignore=shutil.ignore_patterns(
            ".aster", "__pycache__", ".pytest_cache", ".git", "node_modules", "*.pyc"
        ),
    )


class TimedModel(ModelClient):
    def __init__(self, settings):
        super().__init__(settings)
        self.requests = []
        self.response_structure = None

    async def _post(self, suffix, payload, extra):
        data = await super()._post(suffix, payload, extra)
        # Observe schema only; never dump model reasoning, credentials or response bodies.
        self.response_structure = {
            "keys": sorted(data),
            "response_id": data.get("id"),
            "status": data.get("status"),
            "output_type": type(data.get("output")).__name__,
            "items": [
                {
                    "type": item.get("type"),
                    "keys": sorted(item),
                    "content_types": [
                        block.get("type")
                        for block in item.get("content", [])
                        if isinstance(block, dict)
                    ],
                }
                for item in data.get("output", [])
                if isinstance(item, dict)
            ]
            if isinstance(data.get("output"), list)
            else [],
        }
        return data

    async def complete(self, messages, tools, *, on_event=None):
        start = time.monotonic()
        self.response_structure = None
        record = {"status": "started", "http_attempts": None}
        try:
            reply = await super().complete(messages, tools, on_event=on_event)
            record.update(
                status="completed", usage=reply.usage, response_model=reply.provider_model
            )
            return reply
        except BaseException as exc:
            record.update(status="failed", error_type=type(exc).__name__, usage=self.last_usage)
            if hasattr(exc, "details"):
                record["category"] = exc.details.get("category")
                record["http_attempts"] = exc.details.get("attempts")
            raise
        finally:
            record["seconds"] = time.monotonic() - start
            record["response_structure"] = self.response_structure
            self.requests.append(record)


async def run(args):
    if Path(sys.executable).resolve() != PYTHON.resolve():
        raise ValueError("Use the aster Python environment")
    commit = subprocess.check_output(
        ["git", "rev-parse", args.source_commit], cwd=REPO, text=True
    ).strip()
    if subprocess.check_output(["git", "diff", commit, "--", "src/aster"], cwd=REPO):
        raise ValueError("Product sources differ from frozen source commit")
    import aster

    if Path(aster.__file__).resolve() != REPO / "src/aster/__init__.py":
        raise ValueError("Aster was imported from a different source tree")
    source_before = digest_files(REPO / "src/aster", "**/*.py")
    destination = HERE / "evidence" / args.name
    root = HERE / "workspaces" / args.name
    result_path = HERE / "results" / f"{args.name}.json"
    if destination.exists() or root.exists() or result_path.exists():
        raise ValueError("Refusing to overwrite an existing attempt")
    if args.seed:
        copy_product(args.seed.resolve(), root)
    else:
        root.mkdir(parents=True)
        shutil.copytree(HERE / "rules", root / "docs")
    if args.skill:
        shutil.copytree(HERE / "skill", root / ".aster/skills")
    if args.memory_seed:
        # Copies verified memory together with its original trace provenance, not model code.
        shutil.copytree(args.memory_seed / ".aster", root / ".aster", dirs_exist_ok=True)
    product_before = {
        path: digest
        for path, digest in digest_files(root).items()
        if not path.startswith(".aster/")
    }
    destination.mkdir(parents=True)
    prompt_path = args.prompt.resolve()
    prompt = prompt_path.read_text()
    settings = load_settings(
        REPO / ".env",
        model="gpt-5.6-sol",
        protocol="responses",
        context_tokens=128000,
        output_tokens=4096 if args.read_only else 32768,
        request_timeout=180,
        stream=True,
        sandbox="required",
        approval="auto",
        network=False,
        read_only=args.read_only,
        memory_enabled=args.memory,
        memory_evolution=False,
        auto_dream=False,
        max_workers=0,
        read_parallelism=1,
    )
    model = TimedModel(settings)
    runtime = Runtime(root, settings, model=model, approval_callback=lambda *_: True)
    task = TaskSpec(
        prompt=prompt,
        max_steps=args.steps,
        max_turns=args.turns,
        require_edit=not args.read_only,
        require_verification=not args.read_only,
        required_artifacts=[] if args.read_only else ["index.html", "README.md"],
    )
    docs_before = digest_files(root / "docs")
    protocol_files = [HERE / "PROTOCOL.md", Path(__file__), prompt_path, HERE / "check_browser.py"]
    launch = {
        "source_commit": commit,
        "source_files": source_before,
        "name": args.name,
        "seed": str(args.seed) if args.seed else None,
        "configuration": settings.identity(),
        "task": task.model_dump(),
        "wall_seconds": args.seconds,
        "python": sys.executable,
        "protocol_files": {
            str(p.relative_to(HERE)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in protocol_files
        },
        "docs": docs_before,
        "initial_product": product_before,
        "skill": args.skill,
        "memory": args.memory,
        "memory_setup": "operator_seeded" if args.memory_seed else "natural_or_disabled",
    }
    write_json(destination / "launch.json", runtime.evidence.redactor.value(launch))

    def progress(row):
        if row["event"] == "tool_result":
            print(
                json.dumps(
                    {
                        "name": args.name,
                        "step": row.get("step"),
                        "tool": row["call"]["name"],
                        "status": row["outcome"]["status"],
                        "reason": row["outcome"]["reason"],
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
        elif row["event"] in {"model_reply", "provider_error", "run_completed", "run_stopped"}:
            print(json.dumps({"name": args.name, "event": row["event"]}), flush=True)

    runtime.evidence.listeners.append(progress)
    start = time.monotonic()
    error = None
    try:
        await asyncio.wait_for(runtime.run(task), timeout=args.seconds)
    except (Exception, asyncio.CancelledError) as exc:
        error = type(exc).__name__
    state = runtime.state
    events = list(runtime.evidence.events(state.run_id))
    calls = [e for e in events if e["event"] == "tool_result"]
    summary = {
        "name": args.name,
        "source_commit": commit,
        "workspace": str(root.relative_to(REPO)),
        "run_id": state.run_id,
        "session_id": state.session_id,
        "phase": state.phase,
        "stop_reason": state.stop_reason,
        "steps": state.step,
        "turns": state.turn,
        "seconds": time.monotonic() - start,
        "error": error,
        "usage": state.usage,
        "requests": model.requests,
        "verification": state.verification.model_dump(),
        "final": runtime.evidence.redactor.text(state.final),
        "source_unchanged": digest_files(REPO / "src/aster", "**/*.py") == source_before,
        "docs_unchanged": digest_files(root / "docs") == docs_before,
        "tool_results": [
            {
                "tool": e["call"]["name"],
                "status": e["outcome"]["status"],
                "reason": e["outcome"]["reason"],
            }
            for e in calls
        ],
        "memory_lessons": len(runtime.memory.state["lessons"]),
        "memory_store_attempts": sum(e["call"]["name"] == "memory_store" for e in calls),
        "memory_store_successes": sum(
            e["call"]["name"] == "memory_store" and e["outcome"]["status"] == "success"
            for e in calls
        ),
        "active_skills": state.active_skills,
        "read_file_calls": sum(e["call"]["name"] == "read_file" for e in calls),
        "search_calls": sum(e["call"]["name"] == "search" for e in calls),
        "operator_candidate_edits": 0,
        "operator_feedback": args.feedback,
    }
    write_json(result_path, runtime.evidence.redactor.value(summary))
    print(
        json.dumps(
            {
                k: summary[k]
                for k in ["name", "phase", "stop_reason", "steps", "turns", "seconds", "error"]
            }
        ),
        flush=True,
    )
    return (
        0
        if state.phase == "completed" and summary["source_unchanged"] and summary["docs_unchanged"]
        else 1
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("name")
    parser.add_argument("--prompt", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--seed", type=Path)
    parser.add_argument("--memory-seed", type=Path)
    parser.add_argument("--memory", action="store_true")
    parser.add_argument("--skill", action="store_true")
    parser.add_argument("--read-only", action="store_true")
    parser.add_argument("--feedback", type=int, default=0)
    parser.add_argument("--steps", type=int, default=55)
    parser.add_argument("--turns", type=int, default=35)
    parser.add_argument("--seconds", type=int, default=1200)
    arguments = parser.parse_args()
    if not arguments.name.replace("-", "").replace("_", "").isalnum():
        parser.error("name must use letters, digits, hyphens and underscores")
    raise SystemExit(asyncio.run(run(arguments)))
