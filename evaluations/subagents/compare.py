"""Compare every planned pair, keeping service failures and unknown usage visible."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from statistics import median

from analyze import answer_rows, value
from evaluate import HERE, OUT, completed_result, digest, hashes, save, usage_complete


def metrics(rows):
    arms = {arm: [r for r in rows if r["arm"] == arm] for arm in ("solo", "team")}
    indexed = {(r["task"], r["repeat"], r["arm"]): r for r in rows}
    assert len(indexed) == len(rows)
    pairs = [(s, indexed[s["task"], s["repeat"], "team"]) for s in arms["solo"]]
    assert len(pairs) * 2 == len(rows)
    parsed = [(s, t) for s, t in pairs if not s["grade"]["parse_error"] and not t["grade"]["parse_error"]]
    complete = [(s, t) for s, t in pairs if usage_complete(s) and usage_complete(t)]
    tokens = {arm: sum(pair[i]["usage"]["total_tokens"] for pair in complete)
              for i, arm in enumerate(("solo", "team"))}
    token_ratios = [t["usage"]["total_tokens"] / s["usage"]["total_tokens"]
                    for s, t in complete if s["usage"]["total_tokens"]]
    return dict(
        arms={arm: dict(
            episodes=len(group), strict_pass=sum(r["passed"] for r in group),
            behavior_facts=sum(r["behavior_correct"] for r in group),
            strict_facts=sum(r["grade"]["correct"] for r in group),
            parse_errors=sum(r["grade"]["parse_error"] for r in group),
            reported_tokens=sum(r["usage"].get("total_tokens", 0) for r in group),
            usage_complete=all(usage_complete(r) for r in group),
            model_calls=sum(r["model_calls"] for r in group),
            parent_calls=sum(r["parent_model_calls"] for r in group),
            parent_reported_input=sum(r["parent_input_tokens"] for r in group),
            seconds=sum(r["seconds"] for r in group),
        ) for arm, group in arms.items()},
        strict_pairs=dict(wins=sum(t["passed"] > s["passed"] for s, t in pairs),
                          losses=sum(t["passed"] < s["passed"] for s, t in pairs),
                          ties=sum(t["passed"] == s["passed"] for s, t in pairs)),
        both_parseable=dict(pairs=len(parsed), facts=sum(s["grade"]["total"] for s, _ in parsed),
                            behavior={arm: sum(pair[i]["behavior_correct"] for pair in parsed)
                                      for i, arm in enumerate(("solo", "team"))}),
        complete_usage=dict(pairs=len(complete), tokens=tokens,
                            ratio_of_totals=tokens["team"] / tokens["solo"] if tokens["solo"] else None,
                            median_token_ratio=median(token_ratios) if token_ratios else None),
        median_seconds_ratio=median(t["seconds"] / s["seconds"] for s, t in pairs),
    )


def compare(round_id):
    dest = OUT / round_id
    frozen = json.loads((dest / "freeze.json").read_text())
    assert hashes(dest / "src") == frozen["source"]
    assert all(digest(dest / "original-harness" / name) == sha for name, sha in frozen["harness"].items())
    assert digest(dest / "preflight.json") == frozen["preflight_sha256"]
    gold = {r["task"]: {row["id"]: row for row in r["gold"]}
            for r in json.loads((dest / "preflight.json").read_text())}
    diagnostics = json.loads((HERE / round_id / "diagnostics.json").read_text())
    assert diagnostics["episodes"] == diagnostics["planned"] == len(frozen["order"])
    assert [{k: row[k] for k in ("task", "arm", "repeat")} for row in diagnostics["diagnostics"]] == frozen["order"]
    artifacts = json.loads((HERE / round_id / "TRACE-HASHES.json").read_text())
    assert all(digest(OUT / name) == sha for name, sha in artifacts.items())
    rows, child_facts, child_correct, batches = [], 0, 0, []
    child_unique_facts, delegated_episodes, completed_child_runs = set(), 0, 0
    expected_artifacts = set()
    for planned in frozen["order"]:
        name = f"{planned['task']}--{planned['arm']}--{planned['repeat']}"
        result = completed_result(dest / "runs" / name / "result.json", planned, frozen)
        rows.append(result)
        children = [run for run in result["runs"] if run["parent_run_id"]]
        delegated_episodes += bool(children)
        completed_child_runs += sum(run["phase"] == "completed" for run in children)
        expected_artifacts.update(str((dest / "runs" / name / filename).relative_to(OUT))
                                  for filename in ("result.json", "request-ledger.json", "launch.json"))
        for run in result["runs"]:
            folder = dest / "workspaces" / name / ".aster/runs" / run["run_id"]
            expected_artifacts.add(str((folder / "trace.jsonl").relative_to(OUT)))
            if run["parent_run_id"]:
                if (folder / "handoff.json").exists():
                    handoff = json.loads((folder / "handoff.json").read_text())
                    for key, row in answer_rows(handoff["answer"]).items():
                        child_facts += 1
                        child_correct += key in gold[planned["task"]] and value(row) == value(gold[planned["task"]][key])
                        if key in gold[planned["task"]]:
                            child_unique_facts.add((name, key))
                continue
            for line in (folder / "trace.jsonl").read_text().splitlines():
                event = json.loads(line)
                if event["event"] != "model_reply":
                    continue
                calls = event.get("calls", [])
                waits = [call["arguments"].get("wait_seconds", 0) for call in calls if call["name"] == "worker_status"]
                if waits:
                    batches.append(planned | dict(turn=event["turn"], waits=waits,
                                                  tools=[call["name"] for call in calls]))
    assert set(artifacts) == expected_artifacts, "incomplete_trace_manifest"
    diag = [row for row in diagnostics["diagnostics"] if row["arm"] == "team"]
    coordination = dict(
        parent_lines_before_handoff=sum(r["parent_lines_before_first_handoff"] for r in diag),
        overlapping_lines=sum(r["parent_child_overlap_before_first_handoff"] for r in diag),
        status_waits=dict(Counter(str(wait) for r in diag for wait in r["parent_status_waits"])),
        tool_load_requests=dict(Counter(name for r in diag for name in r["parent_tool_load_requests"])),
        child_facts=child_facts, child_correct=child_correct, propagation=diagnostics["propagation"],
        child_unique_facts=len(child_unique_facts), delegated_episodes=delegated_episodes,
        completed_child_runs=completed_child_runs,
    )
    report = dict(all=metrics(rows),
                  by_repeat={str(repeat): metrics([r for r in rows if r["repeat"] == repeat])
                             for repeat in sorted({r["repeat"] for r in rows})},
                  coordination=coordination, archive_inputs_and_result_identities_verified=True)
    save(HERE / round_id / "comparisons.json", report)
    save(HERE / round_id / "WAIT-BATCHES.json", batches)
    print(json.dumps(report["all"], indent=2))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("round")
    compare(parser.parse_args().round)
