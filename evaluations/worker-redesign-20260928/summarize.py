"""Summarize all three arms; publish numeric results without private traces."""
from __future__ import annotations

import itertools
import json
import statistics
from collections import Counter
from pathlib import Path

from evaluate import ARMS, HERE, OUT, bank, save, sha


def stats(rows):
    usage = sum((Counter(r["usage"]) for r in rows), Counter())
    return {"episodes": len(rows), "passed": sum(r["passed"] for r in rows),
            "runtime_completed": sum(r["phase"] == "completed" for r in rows),
            "seconds": sum(r["seconds"] for r in rows),
            "median_seconds": statistics.median(r["seconds"] for r in rows) if rows else None,
            "usage": dict(usage), "accounting_matches": all(r["usage_matches"] and r["http_usage_matches"] for r in rows),
            **{key: sum(r[key] for r in rows) for key in (
                "model_calls", "tool_calls", "http_attempts", "http_with_total_usage",
                "workers_started", "workers_completed", "partial_handoffs", "persisted_handoffs",
                "automatic_handoffs", "coordination_calls", "scope_conflicts", "parent_input_tokens",
                "parent_tool_calls", "child_tokens", "shared_parent_child_reads")}}


def comparable_settings(launch):
    return {key: value for key, value in launch["settings"].items() if key != "max_workers"}


def main():
    tasks = bank()
    rows = []
    for path in sorted((OUT / "runs").glob("*/result.json")):
        row = json.loads(path.read_text())
        ledger = json.loads((path.parent / "request-ledger.json").read_text())
        accounted = sum((Counter(attempt.get("usage", {})) for request in ledger for attempt in request["attempts"]), Counter())
        row["http_usage_matches"] = accounted == Counter(row["usage"])
        children = [run for run in row["runs"] if run.get("parent_run_id")]
        parents = [run for run in row["runs"] if not run.get("parent_run_id")]
        row["parent_input_tokens"] = sum(run["usage"].get("input_tokens", 0) for run in parents)
        row["child_tokens"] = sum(run["usage"].get("total_tokens", 0) for run in children)
        row["parent_tool_calls"] = sum(not tool["child"] for tool in row["tools"])
        reads = [{tool["arguments"]["path"] for tool in row["tools"] if tool["name"] == "read_file"
                  and tool["status"] == "success" and tool["child"] == child} for child in (False, True)]
        row["shared_parent_child_reads"] = len(reads[0] & reads[1])
        handoffs = [json.loads(p.read_text()) for p in Path(row["workspace"]).glob(".aster/runs/*/handoff.json")]
        row["persisted_handoffs"] = len(handoffs)
        row["partial_handoffs"] = sum(item.get("delivery") == "partial" for item in handoffs)
        row["automatic_handoffs"] = 0
        for trace in Path(row["workspace"]).glob(".aster/runs/*/trace.jsonl"):
            row["automatic_handoffs"] += sum(json.loads(line).get("event") == "worker_handoff_delivered" for line in trace.read_text().splitlines())
        rows.append(row)
    checks = {path.parent.name: json.loads(path.read_text()) for path in (OUT / "preflight").glob("*/preflight.json")}
    triples, pairs = [], []
    for task in tasks:
        found = {r["arm"]: r for r in rows if r["task"] == task["id"]}
        if len(found) != 3:
            continue
        launches = {arm: json.loads((OUT / "runs" / (task["id"] + "--" + arm) / "launch.json").read_text()) for arm in ARMS}
        first = launches[ARMS[0]]
        for arm in ARMS[1:]:
            for key in ("prompt", "endpoint_sha256", "limits", "preparation", "python", "driver_hashes"):
                if launches[arm][key] != first[key]:
                    raise ValueError(f"three_arm_input_mismatch: {task['id']} {key}")
            if comparable_settings(launches[arm]) != comparable_settings(first) or found[arm]["product_before"] != found[ARMS[0]]["product_before"]:
                raise ValueError("three_arm_environment_mismatch: " + task["id"])
        triples.append(task["id"])
        for left, right in itertools.combinations(ARMS, 2):
            a, b = found[left], found[right]
            pairs.append({"task": task["id"], "evaluation_split": task["evaluation_split"],
                "left": left, "right": right, "left_passed": a["passed"], "right_passed": b["passed"],
                "left_tokens": a["usage"].get("total_tokens"), "right_tokens": b["usage"].get("total_tokens"),
                "left_seconds": a["seconds"], "right_seconds": b["seconds"], "input_identity_verified": True})
    paired = [r for r in rows if r["task"] in triples]
    incomplete = []
    for folder in sorted((OUT / "runs").glob("*")):
        if (folder / "result.json").exists():
            continue
        requests = json.loads((folder / "request-ledger.json").read_text()) if (folder / "request-ledger.json").exists() else []
        incomplete.append({"episode": folder.name, "recorded_model_calls": len(requests),
                           "reported_usage": dict(sum((Counter(r.get("usage", {})) for r in requests), Counter()))})
    summary = {"dataset_tasks": len(tasks), "complete_triples": len(triples), "finished_episodes": len(rows),
        "preflight_ready": sum(check.get("ready", False) for check in checks.values()), "incomplete_episodes": incomplete,
        "all_finished": {arm: stats([r for r in rows if r["arm"] == arm]) for arm in ARMS},
        "grouped": {field: {value: {arm: stats([r for r in paired if r[field] == value and r["arm"] == arm]) for arm in ARMS}
                            for value in sorted({r[field] for r in paired})}
                    for field in ("evaluation_split", "difficulty", "source", "cohort")},
        "paired_comparisons": {split: {left + "__vs__" + right: {
                "tasks": len(selected := [p for p in pairs if p["evaluation_split"] == split and p["left"] == left and p["right"] == right]),
                "right_wins": sum(not p["left_passed"] and p["right_passed"] for p in selected),
                "right_losses": sum(p["left_passed"] and not p["right_passed"] for p in selected)}
                for left, right in itertools.combinations(ARMS, 2)} for split in ("regression", "holdout")}}
    public_keys = ("task", "source", "difficulty", "cohort", "evaluation_split", "arm", "source_commit", "passed",
        "phase", "stop_reason", "seconds", "usage", "usage_matches", "http_usage_matches", "model_calls", "http_attempts",
        "http_with_total_usage", "tool_calls", "workers_started", "workers_completed", "partial_handoffs", "persisted_handoffs",
        "automatic_handoffs", "coordination_calls", "scope_conflicts", "parent_input_tokens", "parent_tool_calls", "child_tokens",
        "shared_parent_child_reads", "source_unchanged", "error", "response_models")
    save(HERE / "results.json", [{k: row.get(k) for k in public_keys} | {"grade_mode": row["grade"].get("mode"),
        "grade_status": row["grade"].get("status"), "missing_tests": len(row["grade"].get("missing_tests", []))} for row in rows])
    save(HERE / "pairs.json", pairs)
    save(HERE / "summary.json", summary)
    save(HERE / "coverage.json", [{"task": t["id"], "evaluation_split": t["evaluation_split"],
        "ready": checks.get(t["id"], {}).get("ready", False), "complete_triple": t["id"] in triples} for t in tasks])
    preflights = []
    for task in tasks:
        check = checks.get(task["id"], {})
        initial, reference = check.get("starter", {}), check.get("reference", {})
        path = OUT / "preflight" / task["id"] / "preflight.json"
        preflights.append({"task": task["id"], "evaluation_split": task["evaluation_split"], "ready": check.get("ready", False),
            "starter_passed": initial.get("passed"), "reference_passed": reference.get("passed"),
            "required_tests": reference.get("expected", reference.get("required_count", reference.get("expected_tests"))),
            "reference_missing_tests": len(reference.get("missing_tests", [])), "mode": reference.get("mode", check.get("mode")),
            "reused_gate": bool(check.get("reused_gate")), "sha256": sha(path.read_bytes()) if path.exists() else None})
    save(HERE / "preflight-summary.json", preflights)
    print(json.dumps({key: summary[key] for key in ("dataset_tasks", "preflight_ready", "complete_triples", "finished_episodes")}))


if __name__ == "__main__":
    main()
