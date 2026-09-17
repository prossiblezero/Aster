"""Operator driver for real Aster tasks; candidate implementations are model-authored."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import shutil
import subprocess
import sys
import time
import traceback
from collections import Counter
from pathlib import Path

from aster.config import load_settings
from aster.runtime import Runtime
from aster.schema import TaskSpec
from aster.workspace import Workspace

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
PROMPTS = {1: "01-build.md", 2: "02-anchors.md", 3: "03-conventions.md"}


def save(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def product_manifest():
    return {
        str(path.relative_to(REPO)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted((REPO / "src" / "aster").rglob("*.py"))
    }


def prepare_workspace(condition: str, stage: int, repair: bool) -> Path:
    root = HERE / "workspaces" / condition
    if not root.exists():
        if stage != 1 or repair:
            raise ValueError("Start with stage 1 before continuing an existing project")
        root.mkdir(parents=True)
        shutil.copyfile(HERE / "prompts" / "PROJECT.md", root / "PROJECT.md")
    elif stage == 1 and not repair:
        raise ValueError(f"Refusing to reset existing project: {root}")
    if not repair:
        requirements = (HERE / "requirements.md").read_text()
        if stage == 1:
            requirements = requirements.split("## 阶段 2")[0]
        elif stage == 2:
            requirements = requirements.split("## 阶段 3")[0]
        (root / "requirements.md").write_text(requirements)
        if stage == 3:
            project = root / "PROJECT.md"
            text = project.read_text()
            project.write_text(text.replace("默认文档目录是 `docs`", "默认文档目录是 `manual`"))
    return root


def memory_snapshot(root: Path):
    path = root / ".aster" / "memory" / "state.json"
    return json.loads(path.read_text()) if path.exists() else {"lessons": [], "audit": []}


def summarize_memory(before: dict, after: dict):
    prior = {item["id"]: item for item in before.get("lessons", [])}
    rows = []
    for item in after.get("lessons", []):
        old = prior.get(item["id"], {})
        rows.append(
            {
                "id": item["id"],
                "new": not bool(old),
                "text": item["text"],
                "sources": item.get("sources", {}),
                "inactive_reason": item.get("inactive_reason", ""),
                "retrieval_delta": item.get("retrievals", 0) - old.get("retrievals", 0),
                "successful_use_delta": item.get("successful_uses", 0) - old.get("successful_uses", 0),
                "failed_use_delta": item.get("failed_uses", 0) - old.get("failed_uses", 0),
            }
        )
    return rows


async def run_attempt(args, condition: str, dest: Path, attempt: int):
    from grade import grade

    enabled = condition == "memory_on"
    prompt_file = args.repair or HERE / "prompts" / PROMPTS[args.stage]
    prompt = prompt_file.read_text()
    (dest / "prompt.md").write_text(prompt)
    settings = load_settings(
        REPO / ".env",
        model=args.model,
        protocol="responses",
        approval="auto",
        sandbox="required",
        network=False,
        max_workers=0,
        memory_enabled=enabled,
        memory_evolution=enabled,
        auto_dream=enabled,
        context_tokens=args.context_tokens,
        output_tokens=args.output_tokens,
        request_timeout=args.request_timeout,
    )
    task = TaskSpec(
        prompt=prompt,
        max_steps=args.max_steps,
        max_turns=args.max_turns,
        require_observation=True,
        require_edit=True,
        require_verification=True,
        required_artifacts=["mdcheck.py", "README.md", "pyproject.toml"],
    )
    manifest = product_manifest()
    fixed = HERE / "runs" / "fixed-configuration.json"
    common = {
        "model": settings.model,
        "protocol": settings.protocol,
        "context_tokens": settings.effective_context_tokens,
        "output_tokens": settings.effective_output_tokens,
        "max_steps": args.max_steps,
        "max_turns": args.max_turns,
        "request_timeout": settings.request_timeout,
        "product_manifest": manifest,
    }
    if fixed.exists() and json.loads(fixed.read_text()) != common:
        raise ValueError("Experiment configuration/source drift: preserve the original comparison")
    save(fixed, common)
    root = prepare_workspace(condition, args.stage, bool(args.repair))
    before = memory_snapshot(root)
    runtime = Runtime(root, settings, approval_callback=lambda *_: True)
    events = []

    def event(row):
        events.append(row)
        with (dest / "events.jsonl").open("a") as stream:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
        prefix = f"{condition} stage={args.stage} attempt={attempt}"
        if row["event"] == "context_built":
            print(f"{prefix} request input_estimate={row['estimated_tokens']}", flush=True)
        elif row["event"] == "tool_result":
            result = row["outcome"]
            print(
                f"{prefix} step={row['step']} {row['call']['name']} "
                f"{result['status']} {result.get('reason', '')}",
                flush=True,
            )
        elif row["event"] in {"run_completed", "run_stopped", "provider_error"}:
            print(f"{prefix} {row['event']}", flush=True)

    runtime.evidence.listeners.append(event)
    save(
        dest / "launch.json",
        {
            "python": sys.executable,
            "condition": condition,
            "stage": args.stage,
            "attempt": attempt,
            "repair": bool(args.repair),
            "operator": "Codex; generated project implementation belongs to Aster",
            "git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip(),
            "configuration": settings.identity(),
            "task": task.model_dump(),
            "initial_workspace": runtime.workspace.snapshot(),
            "product_manifest": manifest,
        },
    )
    save(dest / "memory-before.json", before)
    started = time.monotonic()
    error = ""
    try:
        await runtime.run(task)
    except Exception as exc:
        error = runtime.evidence.redactor.text(f"{type(exc).__name__}: {exc}")
    finally:
        elapsed = time.monotonic() - started
        state = runtime.state
        after = memory_snapshot(root)
        save(dest / "memory-after.json", after)
        if state:
            shutil.copytree(runtime.evidence.path("runs", state.run_id), dest / "run")
        snapshot = dest / "project"
        for source in Workspace(root).files():
            target = snapshot / source.relative_to(root)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        outcomes = [row for row in events if row["event"] == "tool_result"]
        counts = Counter(row["call"]["name"] for row in outcomes)
        summary = {
            "condition": condition,
            "stage": args.stage,
            "attempt": attempt,
            "repair": bool(args.repair),
            "run_id": state.run_id if state else None,
            "phase": state.phase if state else "failed",
            "stop_reason": state.stop_reason if state else "startup_error",
            "steps": state.step if state else 0,
            "turns": state.turn if state else 0,
            "usage": state.usage if state else {},
            "seconds": round(elapsed, 3),
            "tools": dict(counts),
            "blocked_or_error": [
                {"tool": row["call"]["name"], "reason": row["outcome"]["reason"]}
                for row in outcomes
                if row["outcome"]["status"] != "success"
            ],
            "memory": summarize_memory(before, after),
            "verification": state.verification.model_dump() if state else None,
            "error": error,
            "final": state.final if state else "",
            "source_unchanged": product_manifest() == manifest,
        }
        save(dest / "summary.json", summary)
    # Assertions and fixtures live outside the model's writable workspace.
    result = await grade(root, args.stage)
    save(dest / "grade.json", result)
    summary["independent_grade"] = result
    save(dest / "summary.json", summary)
    print(
        json.dumps(
            {key: summary[key] for key in ("condition", "stage", "attempt", "phase", "stop_reason", "seconds", "usage", "tools")}
            | {"independent_passed": result["passed"], "evidence": str(dest.relative_to(REPO))},
            ensure_ascii=False,
        ),
        flush=True,
    )
    return summary["phase"] == "completed" and result["passed"] and summary["source_unchanged"]


async def run_one(args, condition: str):
    run_root = HERE / "runs" / condition
    run_root.mkdir(parents=True, exist_ok=True)
    attempt = 1
    while (run_root / f"stage-{args.stage}-attempt-{attempt:02}").exists():
        attempt += 1
    dest = run_root / f"stage-{args.stage}-attempt-{attempt:02}"
    dest.mkdir()
    try:
        return await run_attempt(args, condition, dest, attempt)
    except Exception as exc:
        # Preserve diagnostics without logging exception values that may contain credentials.
        save(dest / "operator-error.json", {
            "type": type(exc).__name__,
            "frames": [
                {"file": frame.filename, "line": frame.lineno, "function": frame.name}
                for frame in traceback.extract_tb(exc.__traceback__)
            ],
        })
        print(f"{condition} operator_error={type(exc).__name__} evidence={dest.relative_to(REPO)}", flush=True)
        return False


async def main_async(args):
    conditions = ["memory_on", "memory_off"] if args.condition == "both" else [args.condition]
    results = await asyncio.gather(*(run_one(args, condition) for condition in conditions))
    return 0 if all(results) else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", type=int, choices=(1, 2, 3), required=True)
    parser.add_argument("--condition", choices=("both", "memory_on", "memory_off"), default="both")
    parser.add_argument("--repair", type=Path, help="Explicit operator feedback; retained as a separate attempt")
    parser.add_argument("--model", default="gpt-5.6-sol")
    parser.add_argument("--context-tokens", type=int, default=128000)
    parser.add_argument("--output-tokens", type=int, default=32768)
    parser.add_argument("--max-steps", type=int, default=80)
    parser.add_argument("--max-turns", type=int, default=50)
    parser.add_argument("--request-timeout", type=float, default=300)
    return asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
