"""One frozen real-model attempt; operator owns this driver and hidden checks only."""
import asyncio
import json
import os
import sys
import time
from pathlib import Path

import benchmark_support

from aster.config import load_settings
from aster.runtime import Runtime
from aster.schema import TaskSpec

HERE = Path(__file__).resolve().parent


async def main():
    root = HERE / "candidate"
    if root.exists():
        raise ValueError("Refusing to overwrite an existing attempt")
    root.mkdir()
    settings = load_settings(HERE.parents[1] / ".env", model="gpt-5.6-sol", protocol="responses",
        context_tokens=128000, output_tokens=32768, request_timeout=300,
        sandbox="required", approval="auto", network=False,
        memory_enabled=False, memory_evolution=False, auto_dream=False, max_workers=0)
    task = TaskSpec(prompt=(HERE / "requirements.md").read_text(), max_steps=30, max_turns=16,
        require_observation=True, require_edit=True, require_verification=True,
        required_artifacts=["csvtotals.py", "README.md"])
    runtime = Runtime(root, settings, approval_callback=lambda *_: True)
    evidence = HERE / "evidence"
    evidence.mkdir()
    (evidence / "launch.json").write_text(json.dumps({
        "product_commit": "ead9f26", "source_path": os.environ["PYTHONPATH"],
        "python": sys.executable, "configuration": settings.identity(), "task": task.model_dump(),
        "attempts": 1, "wall_seconds_limit": 1800, "operator_repairs": 0,
    }, ensure_ascii=False, indent=2))

    def progress(row):
        if row["event"] == "tool_result":
            print(row["step"], row["call"]["name"], row["outcome"]["status"], row["outcome"]["reason"], flush=True)
        elif row["event"] in {"model_reply", "provider_error", "run_completed", "run_stopped"}:
            print(row["event"], flush=True)

    runtime.evidence.listeners.append(progress)
    started = time.monotonic()
    error = ""
    try:
        await asyncio.wait_for(runtime.run(task), 1800)
    except Exception as exc:
        error = type(exc).__name__
    state = runtime.state
    summary = {"product_commit": "ead9f26", "run_id": state.run_id,
        "phase": state.phase, "stop_reason": state.stop_reason, "steps": state.step,
        "turns": state.turn, "usage": state.usage, "seconds": time.monotonic() - started,
        "error": error, "operator_feedback": 0, "operator_repairs": 0}
    (evidence / "runtime-result.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    benchmark_support.DATASETS = HERE
    grade = await benchmark_support.external_check(root, "checks.py")
    summary["independent_grade"] = grade
    (HERE / "result.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return 0 if grade["passed"] and state.phase == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
