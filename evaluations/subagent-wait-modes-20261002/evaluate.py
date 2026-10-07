"""Same-source, first-wait any/all ablation with two repetitions and a Solo control."""
from __future__ import annotations

import argparse
import asyncio
import difflib
import hashlib
import importlib.metadata
import importlib.util
import json
import shutil
import subprocess
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from scoring import preflight

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
OUT = REPO / "artifacts/private/subagent-wait-modes-20261002"
ARMS = ("solo", "any", "all")
ORDER = ((0, 1, 2), (1, 2, 0), (2, 0, 1), (0, 2, 1), (2, 1, 0), (1, 0, 2))
LIMITS = dict(model_calls=36, admitted_tools=60, reported_tokens=300000, seconds=600)
spec = importlib.util.spec_from_file_location("previous_eval", HERE.parent / "subagent-live-20261002/evaluate.py")
shared = importlib.util.module_from_spec(spec)
spec.loader.exec_module(shared)
save, digest, hashes, base = shared.save, shared.digest, shared.hashes, shared.base


def harness_hashes():
    return shared.harness_hashes() | {
        str(path.relative_to(REPO)): digest(path)
        for path in (Path(__file__), HERE / "scoring.py", HERE / "PROTOCOL.md")
    }


def environment():
    packages = sorted((d.metadata["Name"], d.version) for d in importlib.metadata.distributions())
    return dict(python=sys.executable, version=sys.version,
                python_sha256=digest(Path(sys.executable)),
                packages_sha256=hashlib.sha256(json.dumps(packages).encode()).hexdigest())


def freeze():
    if (OUT / "freeze.json").exists():
        raise ValueError("already_frozen")
    assert hashes(OUT / "before/src") == json.loads((OUT / "before/hashes.json").read_text())
    shutil.copytree(REPO / "src/aster", OUT / "candidate/src/aster",
                    ignore=shutil.ignore_patterns("__pycache__"))
    b = base(OUT / "candidate/src")
    checked = preflight(b)
    save(OUT / "preflight.json", checked)
    save(HERE / "PREFLIGHT.json", [{k: v for k, v in row.items() if k != "gold"} for row in checked])
    config = b.settings("parallel")
    if not config.api_key.get_secret_value() and config.auth != "none":
        raise ValueError("configured_model_credential_missing")
    order = [dict(task=t["id"], arm=ARMS[i], repeat=repeat) for repeat in (1, 2)
             for t, indices in zip(b.bank(), ORDER, strict=True)
             for i in (indices if repeat == 1 else reversed(indices))]
    assert Counter((r["arm"], i % 3) for i, r in enumerate(order)) == {
        (arm, position): 4 for arm in ARMS for position in range(3)
    }
    frozen = dict(previous_commit=subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip(),
        limits=LIMITS, harness=harness_hashes(), environment=environment(),
        sources={name: hashes(OUT / name / "src") for name in ("before", "candidate")},
        preflight_sha256=digest(OUT / "preflight.json"), settings=config.identity(),
        endpoint_sha256=hashlib.sha256(config.base_url.encode()).hexdigest(), order=order)
    save(OUT / "freeze.json", frozen)
    save(HERE / "FREEZE.json", frozen)
    patch = []
    for name in frozen["sources"]["candidate"]:
        old, new = OUT / "before/src" / name, OUT / "candidate/src" / name
        patch.extend(difflib.unified_diff(old.read_text().splitlines(True), new.read_text().splitlines(True),
                                          fromfile="before/src/" + name, tofile="candidate/src/" + name))
    (HERE / "candidate.patch").write_text("".join(patch))
    print("Frozen: 36 first episodes, corrected symbol index and scorer controls passed.", flush=True)


def inputs():
    frozen = json.loads((OUT / "freeze.json").read_text())
    source = OUT / "candidate/src"
    assert hashes(source) == frozen["sources"]["candidate"]
    assert harness_hashes() == frozen["harness"] and environment() == frozen["environment"]
    assert digest(OUT / "preflight.json") == frozen["preflight_sha256"]
    b = base(source)
    config = b.settings("parallel")
    assert config.identity() == frozen["settings"]
    assert hashlib.sha256(config.base_url.encode()).hexdigest() == frozen["endpoint_sha256"]
    return frozen, b, config


async def health():
    if (OUT / "provider-health.json").exists():
        raise ValueError("health_already_recorded")
    frozen, b, config = inputs()
    client = b.ModelClient(config.model_copy(update={"output_tokens": 64}))
    attempts, error, success = [], None, False
    try:
        reply = await client.complete([{"role": "user", "content": "Reply with OK only."}], [],
                                      on_event=lambda e: attempts.append(e) if e.get("type") == "diagnostic" else None)
        success = bool(reply.text.strip())
    except Exception as exc:
        error = type(exc).__name__
    result = dict(at=datetime.now(UTC).isoformat(), model=config.model, protocol=config.protocol,
                  stream=config.stream, endpoint_sha256=frozen["endpoint_sha256"], success=success,
                  error_type=error, usage=client.last_usage, attempts=attempts)
    save(OUT / "provider-health.json", result)
    save(HERE / "provider-health.json", {k: v for k, v in result.items() if k != "attempts"})
    print(json.dumps({k: v for k, v in result.items() if k != "attempts"}), flush=True)
    if not success:
        raise RuntimeError("provider_health_failed; batch not started")


def delivery_metrics(runtime, runs, started_at):
    parent = runtime.evidence.events(runtime.state.run_id)
    ends, completed, receipts = [], [], []
    for run in runs:
        if run["parent_run_id"]:
            for event in runtime.evidence.events(run["run_id"]):
                if event["event"] in {"run_completed", "run_stopped"}:
                    ends.append(event["time"])
                if event["event"] == "run_completed":
                    completed.append(event["time"])
    for event in parent:
        if event["event"] == "worker_handoff_delivered":
            receipts.append(event["time"])
        if event["event"] == "tool_result" and event["call"]["name"] == "worker_status":
            data = event["outcome"].get("data", {})
            if any(item.get("delivery") for item in data.get("workers", [data])):
                receipts.append(event["time"])

    def seconds(value):
        return (datetime.fromisoformat(value) - started_at).total_seconds() if value else None

    first_receipt = min(receipts, default=None)
    return dict(first_child_terminal_seconds=seconds(min(ends, default=None)),
                first_child_completed_seconds=seconds(min(completed, default=None)),
                last_child_completed_seconds=seconds(max(completed, default=None)),
                first_receipt_seconds=seconds(first_receipt),
                receipt_delay_seconds=(seconds(first_receipt) - seconds(min(ends)))
                if first_receipt and ends else None,
                first_parent_reply_seconds=seconds(next(
                    (e["time"] for e in parent if e["event"] == "model_reply"), None)))


async def episode(task_id, arm, repeat):
    frozen, b, config = inputs()
    from aster.schema import ToolCall

    task = next(t for t in b.bank() if t["id"] == task_id)
    row = next(r for r in json.loads((OUT / "preflight.json").read_text()) if r["task"] == task_id)
    output = OUT / "runs" / f"{task_id}--{arm}--{repeat}"
    output.mkdir(parents=True, exist_ok=False)
    root = OUT / "workspaces" / output.name
    b.prepare(task, root)
    before = b.ledger_driver.product_hashes(root)
    assert before == row["public_hashes"]
    b.ledger_driver.LIMITS = LIMITS
    ledger = b.ledger_driver.Ledger(output.name, output)
    runtime = b.Runtime(root, config, hooks=b.Hooks([ledger]))
    runtime.start(b.TaskSpec(prompt=b.prompt(task), max_steps=LIMITS["admitted_tools"],
                            max_turns=LIMITS["model_calls"], max_tokens=LIMITS["reported_tokens"],
                            require_observation=True, require_edit=False, require_verification=False))
    save(output / "launch.json", dict(task=task_id, arm=arm, repeat=repeat,
                                      settings=config.identity(), limits=LIMITS,
                                      source_hashes=frozen["sources"]["candidate"]))
    assignments, scheduling = [], {}

    async def execute():
        if arm != "solo":
            for group in task["groups"]:
                assignment = ("Complete only work package " + group["name"] + ". Questions: "
                              + ", ".join(q["id"] for q in group["questions"])
                              + ". Return all assigned answer rows with source evidence using the original JSON contract. "
                              "Do not answer the other package.")
                result = await runtime.workers.start(
                    assignment, mode="explore", explicit=True,
                    parent_work="Integrate both independent evidence reports into the final JSON response.")
                if result.get("status") != "running":
                    raise RuntimeError("explicit_worker_start_failed:" + str(result))
                assignments.append(result)
            start_wait = time.monotonic()
            waited = await runtime.step(ToolCall(name="worker_status", arguments={
                "worker_id": [item["worker_id"] for item in assignments],
                "wait_seconds": 300, "wait_for": arm,
            }))
            scheduling["initial_wait_seconds"] = time.monotonic() - start_wait
            if waited.status != "success":
                raise RuntimeError("initial_status_failed:" + waited.reason)
        await runtime.run()

    started_at, started, error = datetime.now(UTC), time.monotonic(), None
    try:
        with b.observe(ledger, runtime):
            await asyncio.wait_for(execute(), LIMITS["seconds"])
    except (Exception, asyncio.CancelledError) as exc:
        error = type(exc).__name__ + ": " + str(exc)[:300]
    finally:
        await runtime.workers.close()
        runtime.sandbox.close()
    seconds = time.monotonic() - started
    runs, tools, _, usage = b.ledger_driver.collect(runtime, set())
    grade = b.grade(runtime.state.final, row["gold"], task["kind"] == "review")
    intact = before == b.ledger_driver.product_hashes(root)
    source_intact = hashes(OUT / "candidate/src") == frozen["sources"]["candidate"]
    children = [r for r in runs if r.get("parent_run_id")]
    attempts = [a for r in ledger.requests for a in r["attempts"]]
    parent_calls = [r for r in ledger.requests if r.get("role") == "parent"]
    known = Counter()
    for request in ledger.requests:
        known.update(request.get("usage", {}))
    result = dict(task=task_id, arm=arm, repeat=repeat, seconds=seconds, grade=grade, error=error,
                  passed=grade["passed"] and runtime.state.phase == "completed"
                  and intact and source_intact and error is None,
                  phase=runtime.state.phase, stop_reason=runtime.state.stop_reason,
                  workspace_unchanged=intact, source_unchanged=source_intact, usage=usage,
                  ledger_usage=dict(known), usage_matches=usage == dict(known),
                  model_calls=len(ledger.requests), parent_model_calls=len(parent_calls),
                  parent_tools=dict(Counter(t["name"] for t in tools if not t["child"])),
                  tool_calls=len(tools), http_attempts=len(attempts),
                  http_without_total_usage=sum("total_tokens" not in a.get("usage", {}) for a in attempts),
                  http_classifications=dict(Counter(a.get("classification", "") for a in attempts)),
                  response_models=dict(Counter(a.get("response_model", "") for a in attempts)),
                  parent_input_tokens=sum(r.get("usage", {}).get("input_tokens", 0) for r in parent_calls),
                  workers_started=len(children), workers_completed=sum(r["phase"] == "completed" for r in children),
                  assignments=assignments, final=runtime.state.final, runs=runs, tools=tools,
                  evidence=shared.evidence_metrics(runtime, runs),
                  latency=delivery_metrics(runtime, runs, started_at) | scheduling | {
                      "first_parent_request_seconds": parent_calls[0]["started_seconds"] - started
                      if parent_calls else None})
    result = runtime.evidence.redactor.value(result)
    save(output / "result.json", result)
    print(json.dumps({k: result[k] for k in ("task", "arm", "repeat", "passed", "seconds", "usage")}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("freeze", "health", "run", "episode", "summarize"))
    parser.add_argument("--task")
    parser.add_argument("--arm", choices=ARMS)
    parser.add_argument("--repeat", type=int, choices=(1, 2))
    args = parser.parse_args()
    if args.mode == "freeze":
        return freeze()
    if args.mode == "health":
        return asyncio.run(health())
    if args.mode == "episode":
        return asyncio.run(episode(args.task, args.arm, args.repeat))
    if args.mode == "run":
        assert json.loads((OUT / "provider-health.json").read_text())["success"]
        frozen = json.loads((OUT / "freeze.json").read_text())
        for row in frozen["order"]:
            output = OUT / "runs" / f"{row['task']}--{row['arm']}--{row['repeat']}"
            if output.exists():
                if (output / "result.json").exists():
                    continue
                raise ValueError("incomplete_episode_requires_audit:" + str(output))
            with (OUT / "progress.log").open("a") as log:
                subprocess.run([sys.executable, str(Path(__file__)), "episode", "--task", row["task"],
                                "--arm", row["arm"], "--repeat", str(row["repeat"])],
                               stdout=log, stderr=log, check=True)
            result = json.loads((output / "result.json").read_text())
            print(json.dumps({k: result[k] for k in ("task", "arm", "repeat", "passed", "seconds")}), flush=True)
            ledger = json.loads((output / "request-ledger.json").read_text())
            attempts = [a for request in ledger for a in request["attempts"]]
            if attempts and all(a.get("classification") == "request_rejected" for a in attempts):
                raise RuntimeError("provider_rejected_all_requests; stop batch and preserve first attempt")
        return
    results = [json.loads(p.read_text()) for p in sorted((OUT / "runs").glob("*/result.json"))]
    save(HERE / "results.json", results)
    print(f"Saved {len(results)} unmodified first results.")


if __name__ == "__main__":
    main()
