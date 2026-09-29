"""Reconcile the fixed repeated comparison without model calls."""
from __future__ import annotations

import json
import re
from collections import Counter

from evaluate import ARMS, DATA, HERE, OUT, REPEATS, bank, save


def correct_delivery(row):
    return row["passed"] and row["phase"] == "completed" and row["stop_reason"] == "verified" and not row["error"]


def stats(rows):
    usage = sum((Counter(r["usage"]) for r in rows), Counter())
    return {"episodes": len(rows), "passed": sum(r["passed"] for r in rows),
            "correct_normal_delivery": sum(correct_delivery(r) for r in rows), "usage": dict(usage),
            "seconds": sum(r["seconds"] for r in rows),
            **{key: sum(r[key] for r in rows) for key in ("model_calls", "tool_calls", "coordination_calls",
                "workers_started", "workers_completed", "http_attempts", "http_with_total_usage")}}


def main():
    tasks, rows, launches = bank(), [], {}
    frozen = json.loads((OUT / "run-lock.json").read_text()) if (OUT / "run-lock.json").exists() else None
    for path in sorted(OUT.glob("repeat-*/runs/*/result.json")):
        row = json.loads(path.read_text())
        key = row["task"], row["repeat"], row["arm"]
        if (key in launches or row["task"] not in {t["id"] for t in tasks}
                or row["repeat"] not in REPEATS or row["arm"] not in ARMS):
            raise ValueError("duplicate_or_unexpected_episode")
        launch = json.loads((path.parent / "launch.json").read_text())
        if not frozen or launch["driver_hashes"] != frozen["driver_hashes"] or launch["source_commit"] != frozen["sources"][row["arm"]]["commit"]:
            raise ValueError("launch_differs_from_frozen_run")
        ledger = json.loads((path.parent / "request-ledger.json").read_text())
        accounted = sum((Counter(a.get("usage", {})) for r in ledger for a in r["attempts"]), Counter())
        run_usage = sum((Counter(r["usage"]) for r in row["runs"]), Counter())
        row["http_usage_matches"] = accounted == Counter(row["usage"])
        row["parent_child_usage_matches"] = run_usage == Counter(row["usage"])
        if not (row["usage_matches"] and row["http_usage_matches"] and row["parent_child_usage_matches"] and row["source_unchanged"]):
            raise ValueError("source_or_usage_integrity_failure: " + str(key))
        row["correct_normal_delivery"] = correct_delivery(row)
        launches[key] = launch
        rows.append(row)
    pairs = []
    for task in tasks:
        for repeat in REPEATS:
            found = {r["arm"]: r for r in rows if r["task"] == task["id"] and r["repeat"] == repeat}
            if len(found) != 2:
                continue
            a, b = (found[arm] for arm in ARMS)
            left, right = (launches[task["id"], repeat, arm] for arm in ARMS)
            for field in ("prompt", "endpoint_sha256", "limits", "preparation", "python", "driver_hashes",
                          "source_commit", "source_hashes", "source_root", "internal_team_token_budget", "preflight_sha256"):
                if left[field] != right[field]:
                    raise ValueError("paired_input_mismatch: " + task["id"] + " " + field)
            if ({k: v for k, v in left["settings"].items() if k != "max_workers"} !=
                    {k: v for k, v in right["settings"].items() if k != "max_workers"} or a["product_before"] != b["product_before"]):
                raise ValueError("paired_environment_mismatch")
            pairs.append({"task": task["id"], "repeat": repeat, "solo_passed": a["passed"], "worker_passed": b["passed"],
                "both_passed": a["passed"] and b["passed"], "both_correct_normal_delivery": correct_delivery(a) and correct_delivery(b),
                "worker_started": b["workers_started"], "input_identity_verified": True,
                **{arm: {k: found[arm][k] for k in ("seconds", "usage", "model_calls", "tool_calls", "correct_normal_delivery")} for arm in ARMS}})
    summary = {"expected_episodes": 24, "finished_episodes": len(rows), "expected_pairs": 12, "complete_pairs": len(pairs),
        "incomplete_episodes": [str(p.relative_to(OUT)) for p in sorted(OUT.glob("repeat-*/runs/*")) if not (p / "result.json").exists()],
        "all": {arm: stats([r for r in rows if r["arm"] == arm]) for arm in ARMS},
        "tasks": {t["id"]: {arm: stats([r for r in rows if r["task"] == t["id"] and r["arm"] == arm]) for arm in ARMS} for t in tasks},
        "paired_wins": sum(not p["solo_passed"] and p["worker_passed"] for p in pairs),
        "paired_losses": sum(p["solo_passed"] and not p["worker_passed"] for p in pairs)}
    for field in ("both_passed", "both_correct_normal_delivery"):
        eligible = {(p["task"], p["repeat"]) for p in pairs if p[field]}
        summary[field] = {"pairs": len(eligible), **{arm: stats([r for r in rows if r["arm"] == arm and
            (r["task"], r["repeat"]) in eligible]) for arm in ARMS}}
    categories = {t["id"]: t["omission_categories"] for t in json.loads(DATA.read_text())["tasks"]}
    omissions = []
    for row in rows:
        tests = row["grade"].get("fail_to_pass", {}) | row["grade"].get("pass_to_pass", {})
        for category in categories[row["task"]]:
            pattern = category["required_test_regex"]
            selected = {test: status for test, status in tests.items() if pattern and re.search(pattern, test)}
            omissions.append({"task": row["task"], "repeat": row["repeat"], "arm": row["arm"],
                "category": category["id"], "required_subtests": selected,
                "manual_requirement_status": "unknown", "evidence": [],
                "note": "Passing subtests alone do not prove full requirement coverage or worker contribution."})
    save(HERE / "summary.json", summary)
    save(HERE / "pairs.json", pairs)
    save(HERE / "omission-diagnostics.json", omissions)
    save(HERE / "results.json", [{k: v for k, v in r.items() if k not in
        ("tools", "runs", "final", "workspace", "product_before", "product_after", "grade")} | {
        "grade_status": r["grade"].get("status"), "missing_tests": r["grade"].get("missing_tests", [])} for r in rows])
    print(json.dumps({k: summary[k] for k in ("finished_episodes", "complete_pairs", "paired_wins", "paired_losses")}))


if __name__ == "__main__":
    main()
