"""Frozen before/candidate comparison, reusing the existing runner and scorer."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.metadata
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from statistics import median

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
OUT = REPO / "artifacts/private/subagent-handoff-ab-20261002"
PRIOR = REPO / "artifacts/private/subagent-handoff-20261002"
ARMS = ("before", "candidate")
LIMITS = dict(model_calls=36, admitted_tools=60, reported_tokens=300000, seconds=600)


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


shared = load("handoff_shared", HERE.parent / "subagent-live-20261002/evaluate.py")
probes = load("handoff_probes", HERE / "cases.py")
scoring = load("handoff_scoring", HERE.parent / "subagent-wait-modes-20261002/scoring.py")
save, digest, hashes = shared.save, shared.digest, shared.hashes


def harness_hashes():
    return shared.harness_hashes() | {
        str(path.relative_to(REPO)): digest(path)
        for path in (Path(__file__), HERE / "cases.py", HERE / "PROTOCOL.md", Path(scoring.__file__))
    }


def environment():
    packages = sorted((d.metadata["Name"], d.version) for d in importlib.metadata.distributions())
    return dict(python=sys.executable, version=sys.version, python_sha256=digest(Path(sys.executable)),
                packages_sha256=hashlib.sha256(json.dumps(packages).encode()).hexdigest())


def preflight(base):
    records = []
    for task in probes.bank(base):
        with tempfile.TemporaryDirectory(prefix="aster-handoff-oracle-") as folder:
            root = Path(folder) / "workspace"
            base.prepare(task, root)
            gold = []
            for query in task["oracle"]:
                index = scoring.ranges((root / query["evidence_path"]).read_text())
                gold.append(query | {"observed": base.probe(root, task["aliases"], query["code"]),
                                     "lines": index[query["evidence_symbol"]], "evidence_ranges": index})
            answers = [dict(id=row["id"], observed=row["observed"], evidence=dict(
                path=row["evidence_path"], symbol=row["evidence_symbol"], line=row["lines"][0])) for row in gold]
            assert base.grade(json.dumps({"answers": answers}), gold, False)["passed"]
            negatives = [answers[:-1], answers + [answers[0]]]
            for field, value in (("observed", "__wrong__"), ("path", "__wrong__.py"), ("line", 0),
                                 ("line", gold[0]["lines"][1] + 1), ("symbol", "__missing__"),
                                 ("symbol", gold[0]["evidence_symbol"] + " / other")):
                wrong = json.loads(json.dumps(answers))
                target = wrong[0] if field == "observed" else wrong[0]["evidence"]
                target[field] = value
                negatives.append(wrong)
            assert not any(base.grade(json.dumps({"answers": rows}), gold, False)["passed"] for rows in negatives)
            # Check the actual parent task gates before any provider request.
            runtime = base.Runtime(root, base.settings("parallel"))
            runtime.start(base.TaskSpec(prompt=base.prompt(task), require_observation=True,
                                       require_edit=False, require_verification=False))
            assert runtime.state.task.require_observation
            assert not runtime.state.task.require_edit and not runtime.state.task.require_verification
            runtime.sandbox.close()
            records.append(dict(task=task["id"], gold=gold, negative_controls=len(negatives),
                                public_hashes=base.ledger_driver.product_hashes(root)))
    assert sum(len(row["gold"]) for row in records) == 24
    return records


def freeze():
    OUT.mkdir(parents=True, exist_ok=True)
    assert not (OUT / "freeze.json").exists() and not (OUT / "runs").exists()
    previous = json.loads((HERE.parent / "subagent-handoff-20261002/SOURCE-FREEZE.json").read_text())
    for arm in ARMS:
        expected = {path.removeprefix("src/"): sha for path, sha in previous[arm]["files"].items()}
        assert hashes(PRIOR / arm / "src") == expected
        if not (OUT / arm / "src").exists():
            shutil.copytree(PRIOR / arm / "src", OUT / arm / "src", ignore=shutil.ignore_patterns("__pycache__"))
        assert hashes(OUT / arm / "src") == expected
    assert hashes(REPO / "src") == hashes(OUT / "candidate/src")
    base = shared.base(OUT / "candidate/src")
    records = preflight(base)
    save(OUT / "preflight.json", records)
    save(HERE / "PREFLIGHT.json", [{k: v for k, v in row.items() if k != "gold"} for row in records])
    config = base.settings("parallel")
    assert config.api_key.get_secret_value() or config.auth == "none"
    order = [dict(task=task["id"], arm=ARMS[index], repeat=repeat)
             for repeat in (1, 2) for n, task in enumerate(probes.bank(base))
             for index in ((0, 1) if (n + repeat) % 2 else (1, 0))]
    assert Counter((row["arm"], n % 2) for n, row in enumerate(order)) == {
        (arm, position): 3 for arm in ARMS for position in (0, 1)}
    frozen = dict(sources={arm: hashes(OUT / arm / "src") for arm in ARMS},
                  settings=config.identity(), endpoint_sha256=hashlib.sha256(config.base_url.encode()).hexdigest(),
                  harness=harness_hashes(), environment=environment(), limits=LIMITS, order=order,
                  preflight_sha256=digest(OUT / "preflight.json"))
    save(OUT / "freeze.json", frozen)
    save(HERE / "FREEZE.json", frozen)
    print("Frozen: 12 episodes, 24 executable facts, 24 negative controls.", flush=True)


def inputs(arm):
    frozen = json.loads((OUT / "freeze.json").read_text())
    assert harness_hashes() == frozen["harness"] and environment() == frozen["environment"]
    assert digest(OUT / "preflight.json") == frozen["preflight_sha256"]
    for version in ARMS:
        assert hashes(OUT / version / "src") == frozen["sources"][version]
    assert hashes(REPO / "src") == frozen["sources"]["candidate"]
    base = shared.base(OUT / arm / "src")
    config = base.settings("parallel")
    assert config.identity() == frozen["settings"]
    assert hashlib.sha256(config.base_url.encode()).hexdigest() == frozen["endpoint_sha256"]
    return frozen, base, config


def trace_seconds(runtime, runs):
    starts = [event["time"] for event in runtime.evidence.events(runtime.state.run_id)
              if event["event"] == "worker_started"]
    stops = [event["time"] for run in runs for event in runtime.evidence.events(run["run_id"])
             if event["event"] in {"run_completed", "run_stopped"}]
    return (datetime.fromisoformat(max(stops)) - datetime.fromisoformat(min(starts))).total_seconds()


async def health():
    frozen, base, config = inputs("candidate")
    assert not (OUT / "health.json").exists()
    client = base.ModelClient(config)
    attempts, error, success = [], None, False
    try:
        reply = await client.complete([{"role": "user", "content": "Reply OK only."}], [],
                                      on_event=lambda e: attempts.append(e) if e.get("type") == "diagnostic" else None)
        success = bool(reply.text.strip())
    except Exception as exc:
        error = type(exc).__name__
    result = dict(success=success, error_type=error, usage=client.last_usage, attempts=attempts,
                  endpoint_sha256=frozen["endpoint_sha256"])
    save(OUT / "health.json", result)
    save(HERE / "HEALTH.json", {k: v for k, v in result.items() if k != "attempts"})
    print(json.dumps({k: v for k, v in result.items() if k != "attempts"}), flush=True)
    if not success:
        raise RuntimeError("provider_health_failed; batch not started")


async def episode(task_id, arm, repeat):
    frozen, base, config = inputs(arm)
    from aster.schema import ToolCall

    task = next(task for task in probes.bank(base) if task["id"] == task_id)
    checked = next(row for row in json.loads((OUT / "preflight.json").read_text()) if row["task"] == task_id)
    name = f"{task_id}--{arm}--{repeat}"
    output, root = OUT / "runs" / name, OUT / "workspaces" / name
    output.mkdir(parents=True, exist_ok=False)
    base.prepare(task, root)
    before = base.ledger_driver.product_hashes(root)
    assert before == checked["public_hashes"]
    base.ledger_driver.LIMITS = LIMITS
    ledger = base.ledger_driver.Ledger(name, output)
    runtime = base.Runtime(root, config, hooks=base.Hooks([ledger]))
    runtime.start(base.TaskSpec(prompt=base.prompt(task), max_steps=LIMITS["admitted_tools"],
                               max_turns=LIMITS["model_calls"], max_tokens=LIMITS["reported_tokens"],
                               require_observation=True, require_edit=False, require_verification=False))
    assert not runtime.state.task.require_edit and not runtime.state.task.require_verification
    save(output / "launch.json", dict(task=task_id, arm=arm, repeat=repeat, limits=LIMITS,
                                     settings=config.identity(), source_hashes=frozen["sources"][arm]))
    assignments = []

    async def execute():
        for group in task["groups"]:
            assignment = ("Complete only work package " + group["name"] + ". Questions: "
                          + ", ".join(q["id"] for q in group["questions"])
                          + ". Return all assigned answer rows with source evidence using the original JSON contract. "
                          "Do not answer the other package.")
            result = await runtime.workers.start(
                assignment, mode="explore", explicit=True,
                parent_work="Integrate both independent evidence reports into the final JSON response.")
            assert result["status"] == "running"
            assignments.append(result)
        waited = await runtime.step(ToolCall(name="worker_status", arguments={
            "worker_id": [row["worker_id"] for row in assignments], "wait_seconds": 300, "wait_for": "all",
        }))
        assert waited.status == "success"
        await runtime.run()

    started, error = time.monotonic(), None
    try:
        with base.observe(ledger, runtime):
            await asyncio.wait_for(execute(), timeout=LIMITS["seconds"])
    except (Exception, asyncio.CancelledError) as exc:
        error = type(exc).__name__
    finally:
        await runtime.workers.close()
        runtime.sandbox.close()
    seconds = time.monotonic() - started
    runs, tools, _, usage = base.ledger_driver.collect(runtime, set())
    grade = base.grade(runtime.state.final, checked["gold"], False)
    intact = before == base.ledger_driver.product_hashes(root)
    source_intact = hashes(OUT / arm / "src") == frozen["sources"][arm]
    children = [run for run in runs if run["parent_run_id"]]
    attempts = [a for row in ledger.requests for a in row["attempts"]]
    finishes = [tool for tool in tools if tool["name"] == "worker_finish"]
    first = {}
    for call in finishes:
        first.setdefault(call["run_id"], call["status"])
    known = Counter()
    for row in ledger.requests:
        known.update(row.get("usage", {}))
    parent_calls = [row for row in ledger.requests if row.get("role") == "parent"]
    result = dict(task=task_id, arm=arm, repeat=repeat, seconds=seconds, trace_seconds=trace_seconds(runtime, runs), grade=grade, error=error,
                  passed=grade["passed"] and runtime.state.phase == "completed" and intact and source_intact and error is None,
                  phase=runtime.state.phase, stop_reason=runtime.state.stop_reason,
                  workspace_unchanged=intact, source_unchanged=source_intact, usage=usage,
                  ledger_usage=dict(known), usage_matches=usage == dict(known),
                  model_calls=len(ledger.requests), parent_model_calls=len(parent_calls),
                  parent_tools=dict(Counter(tool["name"] for tool in tools if not tool["child"])),
                  workers_started=len(children), workers_completed=sum(run["phase"] == "completed" for run in children),
                  finish_attempts=len(finishes), first_finish_success=sum(status == "success" for status in first.values()),
                  finish_rejections=dict(Counter(call["reason"] for call in finishes if call["status"] != "success")),
                  reference_lengths=dict(Counter(str(len(source["evidence_id"])) for call in finishes
                      for source in call["arguments"].get("sources", []) if isinstance(source, dict)
                      and isinstance(source.get("evidence_id"), str))),
                  http_attempts=len(attempts), http_classifications=dict(Counter(a.get("classification", "") for a in attempts)),
                  response_models=dict(Counter(a.get("response_model", "") for a in attempts)),
                  requests_without_usage=sum("total_tokens" not in row.get("usage", {}) for row in ledger.requests),
                  http_without_usage=sum("total_tokens" not in a.get("usage", {}) for a in attempts),
                  final=runtime.state.final, assignments=assignments, runs=runs, tools=tools,
                  evidence=shared.evidence_metrics(runtime, runs))
    result = runtime.evidence.redactor.value(result)
    save(output / "result.json", result)
    print(json.dumps({k: result[k] for k in ("task", "arm", "repeat", "passed", "seconds", "usage", "finish_rejections")}), flush=True)


def summarize():
    frozen = json.loads((OUT / "freeze.json").read_text())
    rows = [json.loads((OUT / "runs" / f"{item['task']}--{item['arm']}--{item['repeat']}" / "result.json").read_text())
            for item in frozen["order"]]
    groups, pairs = {}, []
    for arm in ARMS:
        selected = [row for row in rows if row["arm"] == arm]
        rejects, references = Counter(), Counter()
        for row in selected:
            rejects.update(row["finish_rejections"])
            references.update(row["reference_lengths"])
        groups[arm] = dict(
            episodes=len(selected), passed=sum(row["passed"] for row in selected),
            facts=sum(row["grade"]["correct"] for row in selected), total_facts=sum(row["grade"]["total"] for row in selected),
            reported_tokens=sum(row["usage"].get("total_tokens", 0) for row in selected),
            requests_without_usage=sum(row["requests_without_usage"] for row in selected),
            known_seconds=sum(row["seconds"] for row in selected if row["seconds"] is not None),
            episodes_without_seconds=sum(row["seconds"] is None for row in selected),
            trace_seconds=sum(row["trace_seconds"] for row in selected),
            model_calls=sum(row["model_calls"] for row in selected),
            parent_model_calls=sum(row["parent_model_calls"] for row in selected),
            finish_attempts=sum(row["finish_attempts"] for row in selected),
            first_finish_success=sum(row["first_finish_success"] for row in selected),
            children=sum(row["workers_started"] for row in selected),
            children_completed=sum(row["workers_completed"] for row in selected),
            finish_rejections=rejects, reference_lengths=references,
            parent_read_calls=sum(row["parent_tools"].get("read_file", 0) for row in selected),
            parent_previously_read_lines=sum(row["evidence"]["parent_lines_previously_read_by_child"] for row in selected),
        )
    for old in (row for row in rows if row["arm"] == "before"):
        new = next(row for row in rows if row["arm"] == "candidate" and (row["task"], row["repeat"]) == (old["task"], old["repeat"]))
        tokens = [row["usage"].get("total_tokens") for row in (old, new)]
        pairs.append(dict(task=old["task"], repeat=old["repeat"], before_passed=old["passed"], candidate_passed=new["passed"],
                          seconds_ratio=new["seconds"] / old["seconds"] if old["seconds"] is not None and new["seconds"] is not None else None,
                          trace_seconds_ratio=new["trace_seconds"] / old["trace_seconds"],
                          token_ratio=tokens[1] / tokens[0] if all(tokens) else None))
    summary = dict(groups=groups, pairs=pairs,
                   paired_seconds_ratio_median=median(row["seconds_ratio"] for row in pairs if row["seconds_ratio"] is not None),
                   paired_trace_seconds_ratio_median=median(row["trace_seconds_ratio"] for row in pairs),
                   paired_reported_token_ratio_median=median(row["token_ratio"] for row in pairs if row["token_ratio"] is not None))
    save(HERE / "results.json", rows)
    save(HERE / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


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
    if args.mode == "summarize":
        return summarize()
    assert json.loads((OUT / "health.json").read_text())["success"]
    for item in json.loads((OUT / "freeze.json").read_text())["order"]:
        path = OUT / "runs" / f"{item['task']}--{item['arm']}--{item['repeat']}"
        if path.exists() and not (path / "result.json").exists():
            raise RuntimeError("incomplete_episode_requires_audit")
        if not path.exists():
            with (OUT / "progress.log").open("a") as log:
                subprocess.run([sys.executable, str(Path(__file__)), "episode", "--task", item["task"],
                                "--arm", item["arm"], "--repeat", str(item["repeat"])],
                               stdout=log, stderr=log, check=True)
        result = json.loads((path / "result.json").read_text())
        if result["http_attempts"] and set(result["http_classifications"]) <= {"rejected"}:
            raise RuntimeError("provider_rejected_all_attempts; stopping batch")
    summarize()


if __name__ == "__main__":
    main()
