"""Describe frozen first-run grades and delivery behavior without repairing answers."""

import json
from collections import Counter
from pathlib import Path
from statistics import median

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PRIVATE = ROOT / "artifacts/private/subagent-live-20261002"
ARMS = ("baseline_parallel", "candidate_solo", "candidate_parallel", "candidate_overlap")


def analyze():
    rows = json.loads((HERE / "results.json").read_text())
    tasks = json.loads((ROOT / "datasets/subagent-light-20260930/instances.json").read_text())
    expected_ids = {t["id"]: {q["id"] for g in t["groups"] for q in g["questions"]} for t in tasks}
    assert len(rows) == 24 and len({(r["task"], r["arm"]) for r in rows}) == 24
    cases, attempts, provider_errors = [], [], Counter()
    attempts_by_arm = {arm: [] for arm in ARMS}
    for row in rows:
        text = row["final"].strip()
        if text.startswith("```") and text.endswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]
        try:
            values = json.loads(text)["answers"]
            ids = [value["id"] for value in values]
            ids_ok = len(ids) == len(set(ids)) and set(ids) == expected_ids[row["task"]]
        except (ValueError, KeyError, TypeError):
            ids_ok = False
        checks = row["grade"].get("checks", [])
        behavior = ids_ok and bool(checks) and all(c["observed"] and c["regression"] for c in checks)
        cases.append(dict(
            task=row["task"], arm=row["arm"], parse_error=row["grade"]["parse_error"],
            behavior_pass=behavior and row["phase"] == "completed" and row["error"] is None
            and row["workspace_unchanged"] and row["source_unchanged"],
            behavior_facts=sum(c["observed"] and c["regression"] for c in checks),
            citation_only=sum(c["observed"] and c["regression"] and not c["evidence"] for c in checks),
            value_errors=[c["id"] for c in checks if not c["observed"]],
            classification_errors=[c["id"] for c in checks if not c["regression"]],
            waits=row["evidence"]["waits"],
        ))
        name = row["task"] + "--" + row["arm"]
        ledger = json.loads((PRIVATE / "runs" / name / "request-ledger.json").read_text())
        cases[-1]["model_calls_by_role"] = dict(Counter(r.get("role", "unknown") for r in ledger))
        episode_attempts = [attempt for request in ledger for attempt in request["attempts"]]
        attempts.extend(episode_attempts)
        attempts_by_arm[row["arm"]].extend(episode_attempts)
        cases[-1]["http_classifications"] = dict(Counter(a.get("classification", "") for a in episode_attempts))
        events_by_run = {}
        for trace in (PRIVATE / "workspaces" / name / ".aster/runs").glob("*/trace.jsonl"):
            events_by_run[trace.parent.name] = events = [json.loads(line) for line in trace.read_text().splitlines()]
            for event in events:
                if event["event"] == "provider_error":
                    error = json.loads(event["error"])
                    provider_errors[str(error.get("status"))] += 1
        parent_run = next(run["run_id"] for run in row["runs"] if not run["parent_run_id"])
        child_ends = [event["time"] for run in row["runs"] if run["parent_run_id"]
                      for event in events_by_run[run["run_id"]] if event["event"] == "run_completed"]
        cases[-1]["parent_tools_before_last_child_completed"] = [
            event["call"]["name"] for event in events_by_run[parent_run]
            if child_ends and event["event"] == "tool_result" and event["time"] < max(child_ends)
        ]
    summary = {}
    for arm in ARMS:
        selected = [r for r in rows if r["arm"] == arm]
        diagnoses = [r for r in cases if r["arm"] == arm]
        summary[arm] = dict(
            behavior_pass=sum(d["behavior_pass"] for d in diagnoses),
            behavior_facts=sum(d["behavior_facts"] for d in diagnoses),
            citation_only=sum(d["citation_only"] for d in diagnoses),
            value_errors=sum(len(d["value_errors"]) for d in diagnoses),
            classification_errors=sum(len(d["classification_errors"]) for d in diagnoses),
            parse_errors=sum(d["parse_error"] for d in diagnoses),
            model_calls=sum(r["model_calls"] for r in selected),
            parent_model_calls=sum(d["model_calls_by_role"].get("parent", 0) for d in diagnoses),
            tool_calls=sum(r["tool_calls"] for r in selected),
            workers_started=sum(r["workers_started"] for r in selected),
            workers_completed=sum(r["workers_completed"] for r in selected),
            selected_characters=sum(r["evidence"]["selected_characters"] for r in selected),
            http_classifications=dict(Counter(a.get("classification", "") for a in attempts_by_arm[arm])),
            non_success_attempt_seconds=sum(a.get("elapsed_seconds", 0) for a in attempts_by_arm[arm]
                                            if a.get("classification") != "success"),
            rejected_handoffs=dict(Counter(
                h["reason"] for r in selected for h in r["evidence"]["handoffs"]
                if h["status"] != "success"
            )),
            median_seconds=median(r["seconds"] for r in selected),
        )
    by_key = {(r["task"], r["arm"]): r for r in rows}
    pairs = {}
    for baseline, candidate in (("baseline_parallel", "candidate_parallel"),
                                ("candidate_solo", "candidate_parallel"),
                                ("candidate_solo", "candidate_overlap")):
        both = [(by_key[(t["id"], baseline)], by_key[(t["id"], candidate)]) for t in tasks]
        pairs[candidate + "_vs_" + baseline] = dict(
            wins=sum(b["passed"] and not a["passed"] for a, b in both),
            losses=sum(a["passed"] and not b["passed"] for a, b in both),
            ties=sum(a["passed"] == b["passed"] for a, b in both),
            median_seconds_ratio=median(b["seconds"] / a["seconds"] for a, b in both),
            median_tokens_ratio=median(b["usage"]["total_tokens"] / a["usage"]["total_tokens"]
                                       for a, b in both if a["usage"].get("total_tokens")),
        )
    accounting = dict(
        http_attempts=len(attempts),
        http_without_total_usage=sum("total_tokens" not in a.get("usage", {}) for a in attempts),
        classifications=dict(Counter(a.get("classification", "") for a in attempts)),
        response_models=dict(Counter(a.get("response_model", "") for a in attempts)),
        provider_errors=dict(provider_errors),
        usage_matches=all(row["usage_matches"] for row in rows),
        source_unchanged=all(row["source_unchanged"] for row in rows),
        workspace_unchanged=all(row["workspace_unchanged"] for row in rows),
        completed_parents=sum(row["phase"] == "completed" for row in rows),
        runner_errors=[dict(task=row["task"], arm=row["arm"], error=row["error"])
                       for row in rows if row["error"]],
    )
    report = dict(summary=summary, pairs=pairs, accounting=accounting, cases=cases)
    (HERE / "diagnostics.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "cases"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    analyze()
