"""Recover the completed first episode after its result writer rejected integer keys."""
from __future__ import annotations

import importlib.util
import json
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("handoff_recovery_runner", HERE / "evaluate.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def recover():
    frozen = json.loads((runner.OUT / "freeze-initial.json").read_text())
    assert runner.digest(runner.OUT / "driver-initial.py") == frozen["harness"][str((HERE / "evaluate.py").relative_to(runner.REPO))]
    base = runner.shared.base(runner.OUT / "before/src")
    name = "django-handoff--before--1"
    output, root = runner.OUT / "runs" / name, runner.OUT / "workspaces" / name
    assert not (output / "result.json").exists()
    checkpoints = [json.loads(path.read_text())["state"] for path in (root / ".aster/runs").glob("*/checkpoint.json")]
    state, = [item for item in checkpoints if not item["parent_run_id"]]
    requests = json.loads((output / "request-ledger.json").read_text())
    assert state["phase"] == "completed" and all(item["status"] == "completed" for item in requests)
    traces = {path.parent.name: [json.loads(line) for line in path.read_text().splitlines()]
              for path in (root / ".aster/runs").glob("*/trace.jsonl")}
    assert next(event["answer"] for event in traces[state["run_id"]] if event["event"] == "run_completed") == state["final"]
    runtime = SimpleNamespace(workspace=SimpleNamespace(root=root),
                              evidence=SimpleNamespace(events=traces.__getitem__), state=SimpleNamespace(**state))
    runs, tools, _, usage = base.ledger_driver.collect(runtime, set())
    checked = next(row for row in json.loads((runner.OUT / "preflight.json").read_text()) if row["task"] == "django-handoff")
    grade = base.grade(state["final"], checked["gold"], False)
    assert base.ledger_driver.product_hashes(root) == checked["public_hashes"]
    assert runner.hashes(runner.OUT / "before/src") == frozen["sources"]["before"]
    children = [run for run in runs if run["parent_run_id"]]
    attempts = [attempt for request in requests for attempt in request["attempts"]]
    finishes = [tool for tool in tools if tool["name"] == "worker_finish"]
    first, known = {}, Counter()
    for call in finishes:
        first.setdefault(call["run_id"], call["status"])
    for request in requests:
        known.update(request["usage"])
    assert dict(known) == usage
    launch = json.loads((output / "launch.json").read_text())
    assignments = [{"worker_id": item["worker_id"], "status": "running"}
                   for item in traces[state["run_id"]] if item["event"] == "worker_started"]
    result = dict(task="django-handoff", arm="before", repeat=1, seconds=None,
                  trace_seconds=runner.trace_seconds(runtime, runs), grade=grade, error=None, passed=grade["passed"],
                  phase=state["phase"], stop_reason=state["stop_reason"],
                  workspace_unchanged=True, source_unchanged=True, usage=usage, ledger_usage=dict(known), usage_matches=True,
                  model_calls=len(requests), parent_model_calls=sum(row["role"] == "parent" for row in requests),
                  parent_tools=dict(Counter(tool["name"] for tool in tools if not tool["child"])),
                  workers_started=len(children), workers_completed=sum(run["phase"] == "completed" for run in children),
                  finish_attempts=len(finishes), first_finish_success=sum(status == "success" for status in first.values()),
                  finish_rejections=dict(Counter(call["reason"] for call in finishes if call["status"] != "success")),
                  reference_lengths=dict(Counter(str(len(source["evidence_id"])) for call in finishes
                                                 for source in call["arguments"].get("sources", []))),
                  http_attempts=len(attempts), http_classifications=dict(Counter(item.get("classification", "") for item in attempts)),
                  response_models=dict(Counter(item.get("response_model", "") for item in attempts)),
                  requests_without_usage=sum("total_tokens" not in row.get("usage", {}) for row in requests),
                  http_without_usage=sum("total_tokens" not in item.get("usage", {}) for item in attempts),
                  final=state["final"], assignments=assignments, runs=runs, tools=tools,
                  evidence=runner.shared.evidence_metrics(runtime, runs),
                  measurement_issue="Initial result writer rejected integer counter keys after the task completed. "
                  "Recovered from original traces without model calls; monotonic elapsed time unavailable.")
    assert launch["source_hashes"] == frozen["sources"]["before"]
    runner.save(output / "result.json", result)
    print(json.dumps({key: result[key] for key in ("passed", "seconds", "trace_seconds", "usage", "finish_rejections")}))


if __name__ == "__main__":
    recover()
