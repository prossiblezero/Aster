"""One baseline/new comparison; reuse Runtime, its trace and its sandbox.

Run each arm in a separate process with PYTHONPATH pointing at its source snapshot.
No memory seeding, LLM judge, retries of completed tasks, or metric fitting.
The new arm uses the same 15-second maintenance drain as the CLI; it counts in cost/latency.
"""

# ruff: noqa: E402
from __future__ import annotations

import argparse
import asyncio
import gzip
import hashlib
import json
import shlex
import sys
import time
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "scripts"))
from benchmark_support import usage_metrics
from cases import CASES

import aster
from aster.config import load_settings
from aster.core.runtime import Runtime
from aster.safety.runner import SandboxRunner
from aster.schema import TaskSpec
from aster.storage.workspace import Workspace


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def put_files(root, files):
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


async def episode(root, settings, prompt, out, *, edit=False, max_turns=20, max_tokens=200000):
    runtime = Runtime(root, settings)
    before = json.loads(json.dumps(runtime.memory.state["lessons"]))
    started = time.monotonic()
    error = None
    foreground_seconds = None
    try:
        await asyncio.wait_for(runtime.run(TaskSpec(
            prompt=prompt, max_steps=30, max_turns=max_turns, max_tokens=max_tokens,
            require_edit=edit, require_verification=edit,
        )), timeout=480)
    except Exception as exc:
        error = type(exc).__name__
    finally:
        foreground_seconds = round(time.monotonic() - started, 3)
        if hasattr(runtime, "maintenance"):
            try:
                await runtime.maintenance.drain(timeout=15)
            finally:
                await runtime.maintenance.close()
        await runtime.workers.close()
        runtime.sandbox.close()
    state = runtime.state
    events = runtime.evidence.events(state.run_id)
    tools = [event for event in events if event["event"] == "tool_result"]
    # request_prepared is not a provider call; model turn count includes failed calls.
    attempts = [event for event in events if event["event"] in {"provider_attempt", "memory_provider"}]
    row = {
        "prompt": prompt, "run_id": state.run_id, "phase": state.phase,
        "stop_reason": state.stop_reason, "error": error, "final": state.final,
        "seconds": round(time.monotonic() - started, 3),
        "foreground_seconds": foreground_seconds,
        "memory_usage": getattr(state, "memory_usage", {}),
        "team_usage": state.team_usage,
        "maintenance_events": [e for e in events if e["event"] == "memory_maintenance"],
        **usage_metrics(state.usage, state.turn + getattr(state, "memory_usage", {}).get("calls", 0)),
        "provider_attempts": len(attempts),
        "provider_attempts_with_total": sum("total_tokens" in e.get("usage", {}) for e in attempts),
        "response_models": sorted({e.get("response_model", "") for e in events if e["event"] == "model_reply"}),
        "tools": [{"name": e["call"]["name"], "arguments": e["call"]["arguments"],
                   "status": e["outcome"]["status"], "reason": e["outcome"]["reason"]} for e in tools],
        "recalled_ids": sorted({ident for e in events if e["event"] == "memory_recalled" for ident in e["ids"]}
                               | {item["id"] for e in tools if e["call"]["name"] == "memory_retrieve"
                                  for item in e["outcome"].get("data", {}).get("lessons", [])}),
        "memory_before": before, "memory_after": runtime.memory.state["lessons"],
        "memory_audit": runtime.memory.state["audit"],
    }
    save(out.with_suffix(".json"), row)
    trace = runtime.evidence.path("runs", state.run_id, "trace.jsonl")
    out.with_suffix(".trace.jsonl.gz").write_bytes(gzip.compress(trace.read_bytes(), mtime=0))
    print(json.dumps({"episode": str(out.relative_to(HERE)), "phase": state.phase,
                      "tokens": row["total_tokens"], "tools": len(tools)}, ensure_ascii=False), flush=True)
    return row


async def grade(root, settings, case):
    # Assertions never enter the agent's workspace or model context. Execute in a
    # disposable sandbox with the same interpreter; no subjective model grading.
    source = (
        "import json\nchecks = " + repr(case["checks"]) + "\nresults = {}\n"
        "for name, check in checks.items():\n"
        "    try:\n"
        "        exec(" + repr(case["setup"]) + " + '\\n' + check, {})\n"
        "        results[name] = {'passed': True}\n"
        "    except Exception as exc:\n"
        "        results[name] = {'passed': False, 'error': type(exc).__name__ + ': ' + str(exc)}\n"
        "print(json.dumps(results, ensure_ascii=False))\n"
    )
    sandbox = SandboxRunner(settings)
    try:
        result = await sandbox.run(Workspace(root), "python -c " + shlex.quote(source), 30, readonly=True)
    finally:
        sandbox.close()
    if result.status != "success":
        return {"passed": False, "checks": {}, "error": result.model_dump()}
    checks = json.loads(result.content.strip().splitlines()[-1])
    return {"passed": all(value["passed"] for value in checks.values()), "checks": checks}


async def main(args):
    settings = load_settings(
        REPO / ".env", context_tokens=32768, output_tokens=4096,
        approval="auto", sandbox="required", network=False, completion="strict",
        memory_enabled=True, memory_evolution=True, auto_dream=True,
        max_workers=0, request_timeout=120,
    )
    # Preserve the configured model/protocol; stream only where supported.
    settings.stream = settings.protocol == "responses"
    out = args.output / args.arm
    out.mkdir(parents=True, exist_ok=True)
    environment = {
        "settings": settings.identity(), "python": sys.version,
        "source": str(Path(aster.__file__).resolve()),
        "source_hashes": {str(p.relative_to(Path(aster.__file__).parent)): hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in sorted(Path(aster.__file__).parent.rglob("*.py"))},
        "case_sha256": hashlib.sha256((HERE / "cases.py").read_bytes()).hexdigest(),
        "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    if (out / "environment.json").exists():
        previous = json.loads((out / "environment.json").read_text())
        assert previous["source_hashes"] == environment["source_hashes"], "source changed"
        assert previous["case_sha256"] == environment["case_sha256"], "cases changed"
    else:
        save(out / "environment.json", environment)
    results = []
    for case in CASES:
        if args.case and case["id"] != args.case:
            continue
        target = out / case["id"]
        if (target / "result.json").exists():
            results.append(json.loads((target / "result.json").read_text()))
            continue
        target.mkdir(exist_ok=True)
        root = args.work / args.arm / case["id"]
        if not root.exists():
            root.mkdir(parents=True)
            put_files(root, case["files"])
        learning = []
        for index, prompt in enumerate(case["learn"]):
            saved = target / f"learn-{index + 1}.json"
            learning.append(json.loads(saved.read_text()) if saved.exists() else
                            await episode(root, settings, prompt, saved.with_suffix(""),
                                          edit=case.get("learn_edit", False)))
        put_files(root, case.get("reuse_files", {}))
        reuse = await episode(root, settings, case["reuse"], target / "reuse", edit=True,
                              max_turns=case.get("max_turns", 20), max_tokens=case.get("max_tokens", 200000))
        check = await grade(root, settings, case)
        learned_ids = {item["id"] for item in learning[-1]["memory_after"] if not item.get("inactive_reason")}
        recalled = learned_ids & set(reuse["recalled_ids"])
        result = {
            "id": case["id"], "focus": case["focus"], "grade": check,
            "passed": check["passed"] and reuse["phase"] == "completed",
            "learning_passed": all(row["phase"] == "completed" for row in learning),
            "learned_memories": len(learned_ids), "recalled_learned_ids": sorted(recalled),
            "reuse_with_recall": check["passed"] and reuse["phase"] == "completed" and bool(recalled),
            "learning_tokens": sum(row["total_tokens"] for row in learning if row["total_tokens"] is not None),
            "reuse_tokens": reuse["total_tokens"],
            "usage_complete": all(row["usage_coverage"]["total_tokens"]["complete"] for row in [*learning, reuse]),
            "reuse_tools": dict(Counter(call["name"] for call in reuse["tools"])),
            "reuse_failed_tests": sum(call["name"] == "run_tests" and call["reason"] == "tests_failed" for call in reuse["tools"]),
            "workspace": str(root),
        }
        save(target / "result.json", result)
        save(target / "product.json", {str(p.relative_to(root)): p.read_text() for p in Workspace(root).files()
                                       if p.suffix in {".py", ".md", ".txt", ".json"}})
        results.append(result)
        save(out / "results.json", results)
        print(json.dumps({"case": case["id"], "arm": args.arm, "passed": result["passed"],
                          "checks": check}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--arm", choices=["baseline", "sleep"], required=True)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=HERE / "results")
    parser.add_argument("--case")
    asyncio.run(main(parser.parse_args()))
