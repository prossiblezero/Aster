"""Reconcile every frozen episode; repeated pass@1, never best-of-two."""
from __future__ import annotations

import itertools
import json
from collections import Counter

from evaluate import ARMS, HERE, OUT, REPEATS, bank, legacy, save, selection


def correct_delivery(row):
    return bool(row["passed"] and row["phase"] == "completed"
                and row["stop_reason"] == "verified" and not row["error"])


def stats(rows):
    usage = sum((Counter(row["usage"]) for row in rows), Counter())
    return {"episodes": len(rows), "passed": sum(row["passed"] for row in rows),
        "correct_normal_delivery": sum(correct_delivery(row) for row in rows), "usage": dict(usage),
        "usage_by_role": {role: dict(sum((Counter(r["usage_by_role"][role]) for r in rows), Counter()))
                          for role in ("parent", "worker")},
        "unknown_http_classifications": dict(sum((Counter(r["unknown_http_classifications"]) for r in rows), Counter())),
        "seconds": sum(row["seconds"] for row in rows),
        "terminations": dict(Counter(str(row["phase"]) + "/" + str(row["stop_reason"]) for row in rows)),
        "episode_errors": dict(Counter(row["error"] for row in rows if row["error"])),
        "grade_errors": sum(row["grade"].get("status") == "grader_error" for row in rows),
        "episodes_with_missing_tests": sum(bool(row["grade"].get("missing_tests")) for row in rows),
        **{key: sum(row[key] for row in rows) for key in ("model_calls", "tool_calls", "coordination_calls",
            "workers_started", "workers_completed", "http_attempts", "http_with_total_usage", "http_without_total_usage")}}


def main():
    tasks, rows, launches = bank(), {}, {}
    task_by_id = {task["id"]: task for task in tasks}
    expected = {(task["id"], repeat, arm) for task in tasks for repeat in REPEATS for arm in ARMS}
    frozen = json.loads((OUT / "run-lock.json").read_text()) if (OUT / "run-lock.json").exists() else None
    for path in sorted(OUT.glob("repeat-*/runs/*/result.json")):
        row = json.loads(path.read_text())
        key = row["task"], row["repeat"], row["arm"]
        if key not in expected or key in rows:
            raise ValueError("duplicate_or_unexpected_episode")
        launch = json.loads((path.parent / "launch.json").read_text())
        arm = row["arm"]
        task = task_by_id[row["task"]]
        if (not frozen or launch["driver_hashes"] != frozen["driver_hashes"]
                or launch["source_commit"] != frozen["sources"][arm]["commit"]
                or launch["source_root"] != frozen["sources"][arm]["root"]
                or launch["source_hashes"] != frozen["source_hashes"][arm]
                or launch["settings"] != frozen["settings"][arm]
                or launch["endpoint_sha256"] != frozen["endpoint_sha256"][arm]
                or launch["preflight_sha256"] != frozen["gates"][row["task"]]["sha256"]):
            raise ValueError("launch_differs_from_frozen_run: " + str(key))
        if (launch["limits"] != legacy.LIMITS[task["difficulty"]]
                or launch["internal_team_token_budget"] != launch["limits"]["reported_tokens"]
                or launch["python"] not in frozen["environments"]
                or launch["aster_import"] != launch["source_root"] + "/src/aster/__init__.py"
                or launch["prompt"] != task["prompt"] + legacy.COMMON + "\n\n" + launch["preparation"].get("prompt_suffix", "")
                or any(row[field] != task[field] for field in ("difficulty", "source"))
                or row["passed"] != bool(row["grade"].get("passed"))):
            raise ValueError("episode_task_or_budget_mismatch: " + str(key))
        for field in ("task", "arm", "repeat", "source_commit", "source_root", "internal_team_token_budget", "preflight_sha256"):
            if row[field] != launch[field]:
                raise ValueError("result_launch_mismatch: " + str(key) + " " + field)
        ledger_path = path.parent / "request-ledger.json"
        ledger = json.loads(ledger_path.read_text()) if ledger_path.exists() else []
        attempts = [attempt for request in ledger for attempt in request["attempts"]]
        accounted = sum((Counter(a.get("usage", {})) for a in attempts), Counter())
        request_usage = sum((Counter(r.get("usage", {})) for r in ledger), Counter())
        run_usage = sum((Counter(r["usage"]) for r in row["runs"]), Counter())
        if not (row["usage_matches"] and row["source_unchanged"]
                and accounted == request_usage == run_usage == Counter(row["usage"]) == Counter(row["ledger_usage"])
                and len(ledger) == row["model_calls"] and len(attempts) == row["http_attempts"]
                and sum("total_tokens" in a.get("usage", {}) for a in attempts) == row["http_with_total_usage"]):
            raise ValueError("source_or_usage_integrity_failure: " + str(key))
        parents = [r for r in row["runs"] if not r.get("parent_run_id")]
        children = [r for r in row["runs"] if r.get("parent_run_id")]
        if ((len(parents) != 1 and (row["runs"] or not row["error"]))
                or len({r["run_id"] for r in row["runs"]}) != len(row["runs"])
                or any(r["parent_run_id"] != parents[0]["run_id"] for r in children)
                or len(children) != row["workers_started"]
                or sum(r["phase"] == "completed" for r in children) != row["workers_completed"]
                or (arm == "solo" and children)
                or len(row["tools"]) != row["tool_calls"]
                or sum(t["name"].startswith("worker_") for t in row["tools"]) != row["coordination_calls"]):
            raise ValueError("parent_child_or_tool_integrity_failure: " + str(key))
        row["usage_by_role"] = {role: dict(sum((Counter(r["usage"]) for r in runs), Counter()))
                               for role, runs in (("parent", parents), ("worker", children))}
        row["http_without_total_usage"] = row["http_attempts"] - row["http_with_total_usage"]
        row["unknown_http_classifications"] = dict(Counter(a.get("classification", "unknown")
            for a in attempts if "total_tokens" not in a.get("usage", {})))
        row["correct_normal_delivery"] = correct_delivery(row)
        launches[key], rows[key] = launch, row

    pairs, triples = [], []
    for task in tasks:
        for repeat in REPEATS:
            found = {arm: rows[task["id"], repeat, arm] for arm in ARMS if (task["id"], repeat, arm) in rows}
            for left, right in itertools.combinations(found, 2):
                a, b = (launches[task["id"], repeat, arm] for arm in (left, right))
                for field in ("prompt", "endpoint_sha256", "limits", "preparation", "python", "driver_hashes",
                              "internal_team_token_budget", "preflight_sha256"):
                    if a[field] != b[field]:
                        raise ValueError("paired_input_mismatch: " + task["id"] + " " + field)
                if ({k: v for k, v in a["settings"].items() if k != "max_workers"} !=
                        {k: v for k, v in b["settings"].items() if k != "max_workers"}
                        or found[left]["product_before"] != found[right]["product_before"]):
                    raise ValueError("paired_environment_mismatch")
                if {left, right} == {"solo", "worker"} and any(a[f] != b[f] for f in ("source_commit", "source_root", "source_hashes")):
                    raise ValueError("new_solo_worker_source_mismatch")
                pairs.append({"task": task["id"], "repeat": repeat, "left": left, "right": right,
                    "input_identity_verified": True, "same_product_source": a["source_commit"] == b["source_commit"],
                    "both_passed": found[left]["passed"] and found[right]["passed"],
                    "both_correct_normal_delivery": correct_delivery(found[left]) and correct_delivery(found[right]),
                    **{arm: {k: found[arm][k] for k in ("passed", "correct_normal_delivery", "seconds", "usage",
                        "model_calls", "tool_calls", "workers_started", "http_without_total_usage")} for arm in (left, right)}})
            if len(found) == len(ARMS):
                triples.append({"task": task["id"], "repeat": repeat,
                    "all_passed": all(r["passed"] for r in found.values()),
                    "all_correct_normal_delivery": all(correct_delivery(r) for r in found.values())})

    values = list(rows.values())
    summary = {"evaluation_split": "development_seen", "estimator": "all independent repeated pass@1 episodes",
        "expected_episodes": len(expected), "finished_episodes": len(rows), "expected_triplets": len(tasks) * len(REPEATS),
        "complete_triplets": len(triples), "missing_episodes": [list(key) for key in sorted(expected - rows.keys())],
        "incomplete_directories": [str(p.relative_to(OUT)) for p in sorted(OUT.glob("repeat-*/runs/*")) if not (p / "result.json").exists()],
        "all": {arm: stats([r for r in values if r["arm"] == arm]) for arm in ARMS},
        "tasks": {task["id"]: {arm: stats([r for r in values if r["task"] == task["id"] and r["arm"] == arm])
            for arm in ARMS} for task in tasks},
        "comparisons": {}, "comparison_policy": selection()["comparison_policy"]}
    for field in ("difficulty", "source"):
        summary[field] = {value: {arm: stats([r for r in values if r[field] == value and r["arm"] == arm])
            for arm in ARMS} for value in sorted({task[field] for task in tasks})}
    for left, right in itertools.combinations(ARMS, 2):
        matching = [p for p in pairs if (p["left"], p["right"]) == (left, right)]
        result = {"complete_pairs": len(matching)}
        for label in ("passed", "correct_normal_delivery"):
            result[label] = {"right_wins": sum(not p[left][label] and p[right][label] for p in matching),
                "right_losses": sum(p[left][label] and not p[right][label] for p in matching),
                "ties": sum(p[left][label] == p[right][label] for p in matching)}
        for label in ("both_passed", "both_correct_normal_delivery"):
            eligible = {(p["task"], p["repeat"]) for p in matching if p[label]}
            result[label] = {"pairs": len(eligible), **{arm: stats([r for r in values if r["arm"] == arm
                and (r["task"], r["repeat"]) in eligible]) for arm in (left, right)}}
        summary["comparisons"][left + "_vs_" + right] = result
    for label in ("all_passed", "all_correct_normal_delivery"):
        eligible = {(p["task"], p["repeat"]) for p in triples if p[label]}
        summary[label] = {"triplets": len(eligible), **{arm: stats([r for r in values if r["arm"] == arm
            and (r["task"], r["repeat"]) in eligible]) for arm in ARMS}}
    summary["worker_use_subgroups_descriptive"] = {arm: {label: stats([r for r in values if r["arm"] == arm
        and bool(r["workers_started"]) == used]) for label, used in (("used", True), ("unused", False))}
        for arm in ("current", "worker")}
    save(HERE / "summary.json", summary)
    save(HERE / "pairs.json", pairs)
    save(HERE / "results.json", [{k: v for k, v in row.items() if k not in
        ("tools", "runs", "final", "workspace", "product_before", "product_after", "grade")} | {
        "grade_status": row["grade"].get("status"), "grade_mode": row["grade"].get("mode"),
        "missing_tests": row["grade"].get("missing_tests", [])} for row in values])
    print(json.dumps({k: summary[k] for k in ("finished_episodes", "complete_triplets")}))


if __name__ == "__main__":
    main()
