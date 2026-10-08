"""Offline accounting and propagation audit; never sends a model request."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict

from evaluate import HERE, OUT, arm_identity, digest, grade, hashes, save, shared


def answer_rows(text):
    try:
        text = text.strip()
        if text.startswith("```") and text.endswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]
        rows = json.loads(text)["answers"]
        assert isinstance(rows, list)
        assert all(isinstance(r, dict) and isinstance(r.get("id"), str) for r in rows)
        assert len({r["id"] for r in rows}) == len(rows)
        return {r["id"]: r for r in rows}
    except (ValueError, KeyError, TypeError, AssertionError):
        return {}


def value(row):
    return json.dumps({k: row[k] for k in ("observed", "regression") if k in row}, sort_keys=True)


def analyze(round_id):
    dest = OUT / round_id
    base = shared.base(dest / "src")
    frozen = json.loads((dest / "freeze.json").read_text())
    assert hashes(dest / "src") == frozen["source"]
    assert digest(dest / "preflight.json") == frozen["preflight_sha256"]
    gold = {r["task"]: r["gold"] for r in json.loads((dest / "preflight.json").read_text())}
    diagnostics, artifact_hashes, starts = [], {}, []
    for planned in frozen["order"]:
        name = f"{planned['task']}--{planned['arm']}--{planned['repeat']}"
        folder = dest / "runs" / name
        if not (folder / "result.json").exists():
            continue
        result = json.loads((folder / "result.json").read_text())
        requests = json.loads((folder / "request-ledger.json").read_text())
        starts.append(requests[0]["started_seconds"])
        launch = json.loads((folder / "launch.json").read_text())
        assert launch["source"] == frozen["source"] and launch["settings"] == arm_identity(frozen, planned["arm"])
        for filename in ("result.json", "request-ledger.json", "launch.json"):
            artifact_hashes[str((folder / filename).relative_to(OUT))] = digest(folder / filename)
        usage = Counter()
        for request in requests:
            usage.update(request.get("usage", {}))
        assert dict(usage) == result["usage"] == result["ledger_usage"]
        assert len(requests) == result["model_calls"]
        assert sum(r["role"] == "parent" for r in requests) == result["parent_model_calls"]
        review = planned["task"].endswith("-review")
        assert grade(base, result["final"], gold[planned["task"]], review) == result["grade"]
        parents = answer_rows(result["final"])
        children = defaultdict(list)
        root = dest / "workspaces" / name
        checked = next(r for r in json.loads((dest / "preflight.json").read_text()) if r["task"] == planned["task"])
        assert base.ledger_driver.product_hashes(root) == checked["public_hashes"]
        finish_errors, selected_sizes, child_tools = Counter(), [], Counter()
        parent_before_handoff, child_read_lines = set(), set()
        status_waits, parent_loads = [], []
        for run in result["runs"]:
            trace_path = root / ".aster/runs" / run["run_id"] / "trace.jsonl"
            artifact_hashes[str(trace_path.relative_to(OUT))] = digest(trace_path)
            events, previous = [], ""
            for index, line in enumerate(trace_path.read_text().splitlines(), 1):
                event = json.loads(line)
                observed = event.pop("hash")
                assert event["seq"] == index and event["previous"] == previous
                assert hashlib.sha256(json.dumps(event, sort_keys=True, ensure_ascii=False).encode()).hexdigest() == observed
                previous = observed
                events.append(event)
            handed_off = False
            for event in events:
                if event["event"] == "worker_handoff_delivered":
                    handed_off = True
                if event["event"] != "tool_result":
                    continue
                call, outcome = event["call"], event["outcome"]
                data = outcome.get("data", {})
                if not run["parent_run_id"]:
                    if call["name"] == "worker_status":
                        status_waits.append(call["arguments"].get("wait_seconds", 0))
                        handed_off |= any(item.get("delivery") for item in data.get("workers", [data]))
                    elif call["name"] == "tool_load":
                        parent_loads.extend(call["arguments"]["names"])
                if call["name"] == "read_file" and outcome["status"] == "success":
                    lines = {(data["path"], data["source_sha256"], n)
                             for n in range(data["start"], data["end"] + 1)}
                    if run["parent_run_id"]:
                        child_read_lines.update(lines)
                    elif not handed_off:
                        parent_before_handoff.update(lines)
            if not run["parent_run_id"]:
                continue
            handoff_path = trace_path.with_name("handoff.json")
            if handoff_path.exists():
                handoff = json.loads(handoff_path.read_text())
                selected_sizes.append(sum(len(s["content"]) for s in handoff["source_excerpts"]))
                for key, row in answer_rows(handoff["answer"]).items():
                    children[key].append(row)
            for event in events:
                if event["event"] == "tool_result":
                    child_tools[event["call"]["name"]] += 1
                    if event["call"]["name"] == "worker_finish" and event["outcome"]["status"] != "success":
                        finish_errors[event["outcome"]["reason"]] += 1
        propagation = Counter()
        failures = []
        for expected in gold[planned["task"]]:
            key = expected["id"]
            parent = parents.get(key, {})
            correct = value(parent) == value(expected)
            reports = children.get(key, [])
            if len(reports) > 1:
                propagation["multiple_child_reports_unattributed"] += 1
            elif len(reports) == 1:
                child = reports[0]
                child_correct = value(child) == value(expected)
                if not parents:
                    propagation["parent_output_unscorable"] += 1
                elif key not in parents:
                    propagation["parent_omitted_child_answer"] += 1
                elif child_correct:
                    propagation["child_correct_preserved" if correct else "parent_changed_correct_to_wrong"] += 1
                elif correct:
                    propagation["parent_repaired_child_error"] += 1
                else:
                    propagation["parent_repeated_child_error" if value(child) == value(parent)
                                else "both_wrong_different_values"] += 1
            if not correct:
                failures.append(dict(id=key, expected=expected["observed"],
                                     parent=parent.get("observed"),
                                     child=[r.get("observed") for r in reports]))
        diagnostics.append(planned | dict(
            finish_errors=dict(finish_errors), selected_source_characters=selected_sizes,
            child_tools=dict(child_tools), propagation=dict(propagation), behavior_failures=failures,
            parent_status_waits=status_waits, parent_tool_load_requests=parent_loads,
            parent_lines_before_first_handoff=len(parent_before_handoff),
            parent_child_overlap_before_first_handoff=len(parent_before_handoff & child_read_lines),
            failed_checks=[c for c in result["grade"].get("checks", []) if not c["correct"]],
        ))
    assert starts == sorted(starts), "episodes did not follow frozen order"
    total = Counter()
    for row in diagnostics:
        total.update(row["propagation"])
    report = dict(episodes=len(diagnostics), planned=len(frozen["order"]),
                  propagation=dict(total), diagnostics=diagnostics)
    save(HERE / round_id / "diagnostics.json", report)
    save(HERE / round_id / "TRACE-HASHES.json", artifact_hashes)
    print(json.dumps({k: v for k, v in report.items() if k != "diagnostics"}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("round")
    analyze(parser.parse_args().round)
