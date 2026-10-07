"""Live incremental delivery evaluation against the immediately preceding source, four arms."""
from __future__ import annotations

import argparse
import asyncio
import difflib
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
OUT = REPO / "artifacts/private/subagent-live-20261002"
ARMS = ("baseline_parallel", "candidate_solo", "candidate_parallel", "candidate_overlap")
ORDER = ((0, 1, 2, 3), (1, 0, 3, 2), (2, 3, 0, 1), (3, 2, 1, 0), (0, 2, 1, 3), (3, 1, 2, 0))
LIMITS = dict(model_calls=36, admitted_tools=60, reported_tokens=300000, seconds=600)


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def hashes(root):
    return {str(p.relative_to(root)): digest(p) for p in sorted(root.rglob("*.py"))}


def harness_hashes():
    paths = [Path(__file__), REPO / "evaluations/subagent-light-20260930/evaluate.py",
             REPO / "evaluations/worker-memory-20260922/run.py",
             REPO / "evaluations/worker-memory-20260922/cases.py",
             REPO / "scripts/benchmark_support.py",
             *sorted((REPO / "datasets/subagent-light-20260930").glob("*.json")),
             HERE / "PROTOCOL.md"]
    return {str(p.relative_to(REPO)): digest(p) for p in paths}


def base(source):
    sys.path.insert(0, str(source))
    import aster
    if Path(aster.__file__).resolve() != source / "aster/__init__.py":
        raise ValueError("wrong_source_import")
    spec = importlib.util.spec_from_file_location(
        "light_evaluation", REPO / "evaluations/subagent-light-20260930/evaluate.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def freeze():
    if (OUT / "freeze.json").exists():
        raise ValueError("already_frozen")
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip()
    assert hashes(OUT / "baseline/src") == json.loads((OUT / "baseline-hashes.json").read_text())
    shutil.copytree(REPO / "src/aster", OUT / "candidate/src/aster",
                    ignore=shutil.ignore_patterns("__pycache__"))
    b = base(OUT / "candidate/src")
    preflight = []
    for task in b.bank():
        with tempfile.TemporaryDirectory(prefix="aster-evidence-oracle-") as folder:
            root = Path(folder) / "workspace"
            b.prepare(task, root)
            gold = b.oracle(task, root)
            answers = [dict(id=x["id"], observed=x["observed"],
                            evidence=dict(path=x["evidence_path"], symbol=x["evidence_symbol"],
                                          line=x["lines"][0]),
                            **({"regression": x["regression"]} if task["kind"] == "review" else {}))
                       for x in gold]
            assert b.grade(json.dumps(dict(answers=answers)), gold, task["kind"] == "review")["passed"]
            wrong = json.loads(json.dumps(answers))
            wrong[0]["observed"] = "__wrong__"
            bad_citation = json.loads(json.dumps(answers))
            bad_citation[0]["evidence"]["line"] = 0
            for negative in (wrong, bad_citation, answers[:-1], answers + [answers[0]]):
                assert not b.grade(json.dumps(dict(answers=negative)), gold,
                                   task["kind"] == "review")["passed"]
            preflight.append(dict(task=task["id"], gold=gold,
                                  public_hashes=b.ledger_driver.product_hashes(root)))
    config = b.settings("parallel")
    if not config.api_key.get_secret_value() and config.auth != "none":
        raise ValueError("configured_model_credential_missing")
    frozen = dict(previous_commit=commit, limits=LIMITS, harness=harness_hashes(),
                  sources={arm: hashes(OUT / arm / "src") for arm in ("baseline", "candidate")},
                  settings=config.identity(),
                  endpoint_sha256=hashlib.sha256(config.base_url.encode()).hexdigest(),
                  order=[dict(task=t["id"], arm=ARMS[index]) for t, order in
                         zip(b.bank(), ORDER, strict=True) for index in order])
    save(OUT / "preflight.json", preflight)
    save(OUT / "freeze.json", frozen)
    save(HERE / "FREEZE.json", frozen)
    patch = []
    for name in frozen["sources"]["candidate"]:
        old, new = OUT / "baseline/src" / name, OUT / "candidate/src" / name
        patch.extend(difflib.unified_diff(old.read_text().splitlines(True), new.read_text().splitlines(True),
                                          fromfile="baseline/src/" + name, tofile="candidate/src/" + name))
    (HERE / "candidate.patch").write_text("".join(patch))
    print("Frozen: 24 episodes; 48 executable facts and 24 negative controls passed.", flush=True)



async def episode(task_id, arm):
    frozen = json.loads((OUT / "freeze.json").read_text())
    version = "baseline" if arm.startswith("baseline") else "candidate"
    source = OUT / version / "src"
    assert hashes(source) == frozen["sources"][version] and harness_hashes() == frozen["harness"]
    b = base(source)
    from aster.schema import ToolCall

    task = next(t for t in b.bank() if t["id"] == task_id)
    row = next(r for r in json.loads((OUT / "preflight.json").read_text()) if r["task"] == task_id)
    output = OUT / "runs" / (task_id + "--" + arm)
    output.mkdir(parents=True, exist_ok=False)
    root = OUT / "workspaces" / output.name
    b.prepare(task, root)
    before = b.ledger_driver.product_hashes(root)
    assert before == row["public_hashes"]
    settings = b.settings("parallel")
    assert settings.identity() == frozen["settings"]
    assert hashlib.sha256(settings.base_url.encode()).hexdigest() == frozen["endpoint_sha256"]
    b.ledger_driver.LIMITS = LIMITS
    ledger = b.ledger_driver.Ledger(output.name, output)
    runtime = b.Runtime(root, settings, hooks=b.Hooks([ledger]))
    runtime.start(b.TaskSpec(prompt=b.prompt(task), max_steps=LIMITS["admitted_tools"],
                            max_turns=LIMITS["model_calls"], max_tokens=LIMITS["reported_tokens"],
                            require_observation=True, require_edit=False, require_verification=False))
    save(output / "launch.json", dict(task=task_id, arm=arm, settings=settings.identity(),
                                      limits=LIMITS, source_hashes=frozen["sources"][version]))
    assignments = []

    async def execute():
        if arm != "candidate_solo":
            groups = task["groups"][:1] if arm == "candidate_overlap" else task["groups"]
            for group in groups:
                assignment = ("Complete only work package " + group["name"] + ". Questions: "
                              + ", ".join(q["id"] for q in group["questions"])
                              + ". Return all assigned answer rows with source evidence using the original JSON contract. "
                              "Do not answer the other package.")
                result = await runtime.workers.start(
                    assignment, mode="explore", explicit=True,
                    parent_work=(
                        "Independently solve package " + task["groups"][1]["name"] + ": "
                        + ", ".join(q["id"] for q in task["groups"][1]["questions"])
                        + ". Then integrate the child's other package into the complete JSON response."
                        if arm == "candidate_overlap" else
                        "Integrate both independent evidence reports into the final JSON response."))
                if result.get("status") != "running":
                    raise RuntimeError("explicit_worker_start_failed:" + str(result))
                assignments.append(result)
            if arm != "candidate_overlap":
                waited = await runtime.step(ToolCall(name="worker_status", arguments={
                    "worker_id": [item["worker_id"] for item in assignments], "wait_seconds": 300,
                }))
                if waited.status != "success":
                    raise RuntimeError("initial_status_failed:" + waited.reason)
        await runtime.run()

    started, error = time.monotonic(), None
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
    source_intact = hashes(source) == frozen["sources"][version]
    parent_reads = [t["arguments"]["path"] for t in tools if t["name"] == "read_file" and not t["child"]]
    child_reads = {t["arguments"]["path"] for t in tools if t["name"] == "read_file" and t["child"]}
    children = [r for r in runs if r.get("parent_run_id")]
    attempts = [a for r in ledger.requests for a in r["attempts"]]
    known = Counter()
    for request in ledger.requests:
        known.update(request.get("usage", {}))
    evidence = evidence_metrics(runtime, runs)
    result = dict(task=task_id, arm=arm, seconds=seconds, grade=grade, error=error,
                  evidence=evidence,
                  passed=grade["passed"] and runtime.state.phase == "completed"
                  and intact and source_intact and error is None,
                  phase=runtime.state.phase, stop_reason=runtime.state.stop_reason,
                  workspace_unchanged=intact, source_unchanged=source_intact, usage=usage,
                  ledger_usage=dict(known), usage_matches=usage == dict(known),
                  model_calls=len(ledger.requests), tool_calls=len(tools),
                  http_without_total_usage=sum("total_tokens" not in a.get("usage", {}) for a in attempts),
                  response_models=dict(Counter(a.get("response_model", "") for a in attempts)),
                  parent_input_tokens=sum(r.get("usage", {}).get("input_tokens", 0)
                                          for r in ledger.requests if r.get("role") == "parent"),
                  parent_read_calls=len(parent_reads),
                  parent_repeated_child_read_calls=sum(p in child_reads for p in parent_reads),
                  workers_started=len(children), workers_completed=sum(r["phase"] == "completed" for r in children),
                  final=runtime.state.final, runs=runs, tools=tools)
    result = runtime.evidence.redactor.value(result)
    save(output / "result.json", result)
    print(json.dumps({k: result[k] for k in ("task", "arm", "passed", "seconds", "usage")}), flush=True)


def evidence_metrics(runtime, runs):
    reads, handoffs, waits = [], [], []
    for run in runs:
        for event in runtime.evidence.events(run["run_id"]):
            if event["event"] != "tool_result":
                continue
            outcome, call = event["outcome"], event["call"]
            if call["name"] == "read_file" and outcome["status"] == "success":
                reads.append(dict(time=event["time"], child=bool(run["parent_run_id"]),
                                  **{key: outcome["data"][key] for key in ("path", "start", "end", "source_sha256")}))
            if call["name"] == "worker_status" and not run["parent_run_id"]:
                data = outcome.get("data", {})
                snapshots = data.get("workers", [data])
                waits.append(dict(status=data.get("status"), batch="workers" in data,
                                  active=sum(item.get("status") in {"running", "ready"} for item in snapshots),
                                  delivered=sum(bool(item.get("delivery")) for item in snapshots)))
            if call["name"] == "worker_finish":
                handoffs.append(dict(status=outcome["status"], reason=outcome["reason"],
                                     sources=outcome.get("data", {}).get("source_excerpts", [])))
    parent_reads = [row for row in reads if not row["child"]]
    covered, total = 0, 0
    for read in parent_reads:
        lines = set(range(read["start"], read["end"] + 1))
        child_lines = set()
        for previous in reads:
            if (previous["child"] and previous["time"] < read["time"]
                    and previous["path"] == read["path"]
                    and previous["source_sha256"] == read["source_sha256"]):
                child_lines.update(range(previous["start"], previous["end"] + 1))
        covered += len(lines & child_lines)
        total += len(lines)
    parent_events = runtime.evidence.events(runtime.state.run_id)
    calls_before_child_done = 0
    child_completion_times = []
    for run in runs:
        if run["parent_run_id"]:
            events = runtime.evidence.events(run["run_id"])
            child_completion_times.extend(e["time"] for e in events if e["event"] == "run_completed")
    if child_completion_times:
        calls_before_child_done = sum(e["event"] == "model_reply" and e["time"] < max(child_completion_times)
                                      for e in parent_events)
    return dict(parent_read_lines=total, parent_lines_previously_read_by_child=covered,
                finish_attempts=len(handoffs), finish_accepted=sum(h["status"] == "success" for h in handoffs),
                selected_characters=sum(len(s["content"]) for h in handoffs if h["status"] == "success" for s in h["sources"]),
                parent_replies_before_last_child_completed=calls_before_child_done,
                handoffs=handoffs, waits=waits,
                waits_with_ready_and_running=sum(bool(w["active"] and w["delivered"]) for w in waits))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("freeze", "run", "episode", "summarize"))
    parser.add_argument("--task")
    parser.add_argument("--arm", choices=ARMS)
    args = parser.parse_args()
    if args.mode == "freeze":
        return freeze()
    if args.mode == "episode":
        return asyncio.run(episode(args.task, args.arm))
    if args.mode == "run":
        frozen = json.loads((OUT / "freeze.json").read_text())
        for row in frozen["order"]:
            output = OUT / "runs" / (row["task"] + "--" + row["arm"])
            if output.exists():
                if (output / "result.json").exists():
                    continue
                raise ValueError("incomplete_episode_requires_audit:" + str(output))
            with (OUT / "progress.log").open("a") as log:
                subprocess.run([sys.executable, str(Path(__file__)), "episode",
                                "--task", row["task"], "--arm", row["arm"]],
                               stdout=log, stderr=log, check=True)
            result = json.loads((output / "result.json").read_text())
            print(json.dumps({k: result[k] for k in ("task", "arm", "passed", "seconds")}), flush=True)
            ledger = json.loads((output / "request-ledger.json").read_text())
            attempts = [a for request in ledger for a in request["attempts"]]
            if attempts and all(a.get("classification") == "request_rejected" for a in attempts):
                raise RuntimeError("provider_rejected_all_requests; stop batch and preserve first attempt")
        return
    results = [json.loads(p.read_text()) for p in sorted((OUT / "runs").glob("*/result.json"))]
    save(HERE / "results.json", results)
    summary = {}
    for arm in ARMS:
        rows = [r for r in results if r["arm"] == arm]
        summary[arm] = dict(episodes=len(rows), passed=sum(r["passed"] for r in rows),
                            facts=sum(r["grade"]["correct"] for r in rows),
                            seconds=sum(r["seconds"] for r in rows),
                            tokens=sum(r["usage"].get("total_tokens", 0) for r in rows),
                            parent_inputs=sum(r["parent_input_tokens"] for r in rows),
                            parent_reads=sum(r["parent_read_calls"] for r in rows),
                            repeated_reads=sum(r["parent_repeated_child_read_calls"] for r in rows),
                            unknown_usage=sum(r["http_without_total_usage"] for r in rows),
                            parent_read_lines=sum(r["evidence"]["parent_read_lines"] for r in rows),
                            repeated_read_lines=sum(r["evidence"]["parent_lines_previously_read_by_child"] for r in rows),
                            parent_replies_before_last_child_completed=sum(r["evidence"]["parent_replies_before_last_child_completed"] for r in rows),
                            waits_with_ready_and_running=sum(r["evidence"]["waits_with_ready_and_running"] for r in rows),
                            finish_attempts=sum(r["evidence"]["finish_attempts"] for r in rows),
                            finish_accepted=sum(r["evidence"]["finish_accepted"] for r in rows))
    save(HERE / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
