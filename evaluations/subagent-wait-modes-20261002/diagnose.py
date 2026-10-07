"""Analyze all frozen first results, including latency, cost and trace integrity."""

import hashlib
import json
from collections import Counter
from pathlib import Path
from statistics import median

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
PRIVATE = REPO / "artifacts/private/subagent-wait-modes-20261002"
ARMS = ("solo", "any", "all")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def analyze():
    frozen = json.loads((HERE / "FREEZE.json").read_text())
    rows = json.loads((HERE / "results.json").read_text())
    by_key = {(r["task"], r["arm"], r["repeat"]): r for r in rows}
    order = [(r["task"], r["arm"], r["repeat"]) for r in frozen["order"]]
    assert len(rows) == 36 and len(by_key) == 36 and set(by_key) == set(order)
    gold = {r["task"]: r["gold"] for r in json.loads((PRIVATE / "preflight.json").read_text())}
    cases, manifests, first_requests, attempts, requests, continuations = [], {}, [], [], [], []
    for key in order:
        row = by_key[key]
        name = f"{row['task']}--{row['arm']}--{row['repeat']}"
        output = PRIVATE / "runs" / name
        original = json.loads((output / "result.json").read_text())
        assert row == original
        ledger = json.loads((output / "request-ledger.json").read_text())
        requests.extend(ledger)
        first_requests.append(ledger[0]["started_seconds"])
        episode_attempts = [a for request in ledger for a in request["attempts"]]
        attempts.extend(episode_attempts)
        assert len(ledger) == row["model_calls"]
        assert sum(r.get("role") == "parent" for r in ledger) == row["parent_model_calls"]
        known = Counter()
        for request in ledger:
            known.update(request.get("usage", {}))
        assert dict(known) == row["usage"] == row["ledger_usage"]
        text = row["final"].strip()
        if text.startswith("```") and text.endswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]
        try:
            values = json.loads(text)["answers"]
            answer = {v["id"]: v for v in values}
            ids_ok = len(answer) == len(values) and set(answer) == {g["id"] for g in gold[row["task"]]}
        except (ValueError, KeyError, TypeError):
            answer, ids_ok = {}, False
        checks = row["grade"].get("checks", [])
        complete = (row["phase"] == "completed" and row["error"] is None
                    and row["workspace_unchanged"] and row["source_unchanged"])
        behavior_pass = bool(ids_ok and checks and all(c["observed"] and c["regression"] for c in checks))
        problems = []
        for expected in gold[row["task"]]:
            check = next((c for c in checks if c["id"] == expected["id"]), {})
            if not check.get("correct"):
                problems.append(dict(id=expected["id"], check=check, answer=answer.get(expected["id"]),
                                     expected_observed=expected["observed"],
                                     expected_regression=expected.get("regression")))
        manifests[name] = {}
        trace_count = 0
        for path in sorted((PRIVATE / "workspaces" / name / ".aster/runs").glob("*/trace.jsonl")):
            previous = ""
            events = [json.loads(line) for line in path.read_text().splitlines()]
            for i, event in enumerate(events, 1):
                if event["event"] == "run_continued":
                    run = next(r for r in row["runs"] if r["run_id"] == event["run_id"])
                    continuations.append(dict(task=row["task"], arm=row["arm"], repeat=row["repeat"],
                                              run_id=run["run_id"], phase=run["phase"], stop_reason=run["stop_reason"]))
                assert event["previous"] == previous and event["seq"] == i
                observed_hash = event.pop("hash")
                assert hashlib.sha256(json.dumps(event, sort_keys=True, ensure_ascii=False).encode()).hexdigest() == observed_hash
                previous = observed_hash
            trace_count += 1
            manifests[name][str(path.relative_to(PRIVATE))] = digest(path)
        assert trace_count == len(row["runs"])
        for filename in ("result.json", "request-ledger.json", "launch.json"):
            path = output / filename
            manifests[name][str(path.relative_to(PRIVATE))] = digest(path)
        cases.append(dict(task=row["task"], arm=row["arm"], repeat=row["repeat"],
                          behavior_pass=behavior_pass and complete,
                          behavior_facts=sum(c["observed"] and c["regression"] for c in checks),
                          citation_only=sum(c["observed"] and c["regression"] and not c["evidence"] for c in checks),
                          requests_without_usage=sum("total_tokens" not in r.get("usage", {}) for r in ledger),
                          failed_requests_without_diagnostics=sum(r["status"] == "failed" and not r["attempts"] for r in ledger),
                          problems=problems,
                          non_success_attempt_seconds=sum(a.get("elapsed_seconds", 0) for a in episode_attempts
                                                          if a.get("classification") != "success")))
    assert first_requests == sorted(first_requests), "episodes did not follow frozen order"
    summary = {}
    for arm in ARMS:
        selected = [r for r in rows if r["arm"] == arm]
        diagnoses = [c for c in cases if c["arm"] == arm]
        parent_tools = Counter()
        subsequent_wait_modes = Counter()
        for row in selected:
            parent_tools.update(row["parent_tools"])
            waits = [t for t in row["tools"] if not t["child"] and t["name"] == "worker_status"]
            subsequent_wait_modes.update(t["arguments"].get("wait_for", "any") for t in waits[1:])
        latency = {}
        for metric in selected[0]["latency"]:
            values = [r["latency"][metric] for r in selected if r["latency"].get(metric) is not None]
            latency[metric] = median(values) if values else None
        summary[arm] = dict(
            episodes=len(selected), passed=sum(r["passed"] for r in selected),
            facts=sum(r["grade"]["correct"] for r in selected),
            behavior_pass=sum(d["behavior_pass"] for d in diagnoses),
            behavior_facts=sum(d["behavior_facts"] for d in diagnoses),
            citation_only=sum(d["citation_only"] for d in diagnoses),
            parse_errors=sum(r["grade"]["parse_error"] for r in selected),
            malformed_completed_answers=sum(r["grade"]["parse_error"] and r["phase"] == "completed" for r in selected),
            requests_without_usage=sum(d["requests_without_usage"] for d in diagnoses),
            failed_requests_without_diagnostics=sum(d["failed_requests_without_diagnostics"] for d in diagnoses),
            seconds=sum(r["seconds"] for r in selected), median_seconds=median(r["seconds"] for r in selected),
            tokens=sum(r["usage"].get("total_tokens", 0) for r in selected),
            model_calls=sum(r["model_calls"] for r in selected),
            parent_model_calls=sum(r["parent_model_calls"] for r in selected), parent_tools=dict(parent_tools),
            subsequent_wait_modes=dict(subsequent_wait_modes),
            coordination_calls=sum(count for name, count in parent_tools.items() if name.startswith("worker_")),
            parent_input_tokens=sum(r["parent_input_tokens"] for r in selected),
            parent_read_lines=sum(r["evidence"]["parent_read_lines"] for r in selected),
            repeated_read_lines=sum(r["evidence"]["parent_lines_previously_read_by_child"] for r in selected),
            early_waits=sum(r["evidence"]["waits_with_ready_and_running"] for r in selected),
            early_parent_replies=sum(r["evidence"]["parent_replies_before_last_child_completed"] for r in selected),
            workers_started=sum(r["workers_started"] for r in selected),
            workers_completed=sum(r["workers_completed"] for r in selected),
            finish_attempts=sum(r["evidence"]["finish_attempts"] for r in selected),
            finish_accepted=sum(r["evidence"]["finish_accepted"] for r in selected),
            rejected_handoffs=dict(Counter(h["reason"] for r in selected for h in r["evidence"]["handoffs"]
                                          if h["status"] != "success")),
            median_latency=latency,
        )
    pairs = {}
    for baseline, candidate in (("any", "all"), ("solo", "any"), ("solo", "all")):
        paired = [(r, by_key[(r["task"], candidate, r["repeat"])]) for r in rows if r["arm"] == baseline]
        cases_paired = [dict(task=a["task"], repeat=a["repeat"],
                            seconds_ratio=b["seconds"] / a["seconds"],
                            tokens_ratio=b["usage"]["total_tokens"] / a["usage"]["total_tokens"]
                            if a["usage"].get("total_tokens") and "total_tokens" in b["usage"] else None,
                            both_completed=a["phase"] == b["phase"] == "completed" and a["error"] is b["error"] is None,
                            passed_before=a["passed"], passed_after=b["passed"])
                       for a, b in paired]
        pairs[candidate + "_vs_" + baseline] = dict(
            median_seconds_ratio=median(c["seconds_ratio"] for c in cases_paired),
            median_tokens_ratio=median(c["tokens_ratio"] for c in cases_paired if c["tokens_ratio"] is not None),
            token_pairs=sum(c["tokens_ratio"] is not None for c in cases_paired),
            # Supplemental sensitivity only; failures remain in every primary score.
            completed_pairs=sum(c["both_completed"] for c in cases_paired),
            completed_only_median_seconds_ratio=median(c["seconds_ratio"] for c in cases_paired if c["both_completed"]),
            faster=sum(c["seconds_ratio"] < 1 for c in cases_paired),
            cheaper=sum(c["tokens_ratio"] < 1 for c in cases_paired if c["tokens_ratio"] is not None),
            wins=sum(not a["passed"] and b["passed"] for a, b in paired),
            losses=sum(a["passed"] and not b["passed"] for a, b in paired), cases=cases_paired)
    accounting = dict(
        completed_parents=sum(r["phase"] == "completed" for r in rows),
        http_attempts=len(attempts), classifications=dict(Counter(a.get("classification") for a in attempts)),
        response_models=dict(Counter(a.get("response_model", "") for a in attempts)),
        unknown_usage=sum("total_tokens" not in a.get("usage", {}) for a in attempts),
        non_success_attempt_seconds=sum(a.get("elapsed_seconds", 0) for a in attempts if a.get("classification") != "success"),
        requests_without_usage=sum("total_tokens" not in r.get("usage", {}) for r in requests),
        failed_requests_without_diagnostics=[dict(error_type=r.get("error_type"), seconds=r.get("seconds"))
                                             for r in requests if r["status"] == "failed" and not r["attempts"]],
        usage_matches=all(r["usage_matches"] for r in rows),
        source_unchanged=all(r["source_unchanged"] for r in rows),
        workspace_unchanged=all(r["workspace_unchanged"] for r in rows),
        runner_errors=[{k: r[k] for k in ("task", "arm", "repeat", "error")} for r in rows if r["error"]],
        trace_chains_valid=True, frozen_order_verified=True,
    )
    report = dict(summary=summary, pairs=pairs, accounting=accounting, cases=cases, continuations=continuations)
    for name, value in (("diagnostics.json", report), ("TRACE-HASHES.json", manifests), ("summary.json", summary)):
        (HERE / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(dict(summary=summary, pairs={k: {f: v for f, v in p.items() if f != "cases"}
                                               for k, p in pairs.items()}, accounting=accounting), indent=2))


if __name__ == "__main__":
    analyze()
