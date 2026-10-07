"""Audit every frozen first result and separate behavior, citation and provider failures."""
from __future__ import annotations

import importlib.util
import json
from collections import Counter
from pathlib import Path
from statistics import median

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("handoff_ab_runner", HERE / "evaluate.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def analyze():
    frozen, base, _ = runner.inputs("candidate")
    gold = {row["task"]: row["gold"] for row in json.loads((runner.OUT / "preflight.json").read_text())}
    rows = json.loads((HERE / "results.json").read_text())
    assert len(rows) == len(frozen["order"]) == 12
    assert [(row["task"], row["arm"], row["repeat"]) for row in rows] == [
        (row["task"], row["arm"], row["repeat"]) for row in frozen["order"]]
    cases, manifests, starts, groups = [], {}, [], {}
    accounting = Counter()
    for row in rows:
        name = f"{row['task']}--{row['arm']}--{row['repeat']}"
        output, root = runner.OUT / "runs" / name, runner.OUT / "workspaces" / name
        assert row == json.loads((output / "result.json").read_text())
        launch = json.loads((output / "launch.json").read_text())
        assert launch["source_hashes"] == frozen["sources"][row["arm"]]
        assert launch["settings"] == frozen["settings"] and launch["limits"] == frozen["limits"]
        expected = next(item for item in json.loads((runner.OUT / "preflight.json").read_text()) if item["task"] == row["task"])
        assert base.ledger_driver.product_hashes(root) == expected["public_hashes"]
        requests = json.loads((output / "request-ledger.json").read_text())
        starts.append(requests[0]["started_seconds"])
        reported = Counter()
        for request in requests:
            reported.update(request.get("usage", {}))
            accounting["model_requests"] += 1
            accounting["requests_without_usage"] += "total_tokens" not in request.get("usage", {})
            accounting["requests_failed_without_diagnostics"] += request["status"] == "failed" and not request["attempts"]
            for attempt in request["attempts"]:
                accounting["http_attempts"] += 1
                accounting["http_" + attempt["classification"]] += 1
                accounting["http_without_usage"] += "total_tokens" not in attempt.get("usage", {})
        assert dict(reported) == row["usage"] == row["ledger_usage"]
        assert len(requests) == row["model_calls"]
        assert sum(request["role"] == "parent" for request in requests) == row["parent_model_calls"]
        accounting["reported_tokens"] += reported.get("total_tokens", 0)
        actual_grade = base.grade(row["final"], gold[row["task"]], False)
        assert actual_grade == row["grade"]
        text, fence = row["final"].strip(), chr(96) * 3
        if text.startswith(fence) and text.endswith(fence):
            text = text.split("\n", 1)[1].rsplit(fence, 1)[0]
        try:
            values = json.loads(text)["answers"]
            answers = {answer["id"]: answer for answer in values}
            ids_ok = len(answers) == len(values) and set(answers) == {item["id"] for item in gold[row["task"]]}
        except (ValueError, KeyError, TypeError):
            answers, ids_ok = {}, False
        checks = actual_grade.get("checks", [])
        complete = row["phase"] == "completed" and row["error"] is None
        problems = []
        for target in gold[row["task"]]:
            check = next((check for check in checks if check["id"] == target["id"]), {})
            if not check.get("correct"):
                problems.append(dict(id=target["id"], check=check, answer=answers.get(target["id"]),
                                     expected_observed=target["observed"]))
        cases.append(dict(task=row["task"], arm=row["arm"], repeat=row["repeat"],
                          parse_error=actual_grade["parse_error"], id_contract_valid=ids_ok,
                          behavior_pass=bool(complete and ids_ok and checks and all(check["observed"] for check in checks)),
                          behavior_facts=sum(check["observed"] for check in checks),
                          citation_only=sum(check["observed"] and not check["evidence"] for check in checks),
                          problems=problems,
                          request_errors=[dict(role=request["role"], error_type=request.get("error_type"),
                                               seconds=request["seconds"], diagnostics=len(request["attempts"]))
                                          for request in requests if request["status"] != "completed"],
                          complete_transport=all(request["status"] == "completed"
                              and "total_tokens" in request.get("usage", {}) and request["attempts"]
                              and all(attempt["classification"] == "success" and "total_tokens" in attempt.get("usage", {})
                                      for attempt in request["attempts"]) for request in requests),
                          non_success_attempt_seconds=sum(attempt.get("elapsed_seconds", 0)
                              for request in requests for attempt in request["attempts"] if attempt["classification"] != "success")))
        manifests[name] = {str(path.relative_to(runner.OUT)): runner.digest(path)
                           for path in (output / "result.json", output / "request-ledger.json", output / "launch.json")}
        trace_count = 0
        for path in sorted((root / ".aster/runs").glob("*/trace.jsonl")):
            previous = ""
            for index, line in enumerate(path.read_text().splitlines(), 1):
                event = json.loads(line)
                observed = event.pop("hash")
                assert event["seq"] == index and event["previous"] == previous
                assert runner.hashlib.sha256(json.dumps(event, sort_keys=True, ensure_ascii=False).encode()).hexdigest() == observed
                previous = observed
            manifests[name][str(path.relative_to(runner.OUT))] = runner.digest(path)
            trace_count += 1
        assert trace_count == len(row["runs"])
    assert starts == sorted(starts), "Episodes did not follow frozen request order"
    for arm in runner.ARMS:
        selected = [case for case in cases if case["arm"] == arm]
        groups[arm] = dict(behavior_pass=sum(case["behavior_pass"] for case in selected),
                           behavior_facts=sum(case["behavior_facts"] for case in selected),
                           parse_errors=sum(case["parse_error"] for case in selected),
                           citation_only=sum(case["citation_only"] for case in selected))
    # Supplemental sensitivity check; retain every episode in the primary results.
    unaffected_pairs = []
    for old in (row for row in rows if row["arm"] == "before"):
        selected = [case for case in cases if (case["task"], case["repeat"]) == (old["task"], old["repeat"])]
        if not all(case["complete_transport"] for case in selected):
            continue
        new = next(row for row in rows if row["arm"] == "candidate"
                   and (row["task"], row["repeat"]) == (old["task"], old["repeat"]))
        unaffected_pairs.append(dict(task=old["task"], repeat=old["repeat"],
            token_ratio=new["usage"]["total_tokens"] / old["usage"]["total_tokens"],
            trace_seconds_ratio=new["trace_seconds"] / old["trace_seconds"],
            seconds_ratio=new["seconds"] / old["seconds"] if old["seconds"] is not None and new["seconds"] is not None else None))
    sensitivity = {}
    for key in ("token_ratio", "trace_seconds_ratio", "seconds_ratio"):
        values = [pair[key] for pair in unaffected_pairs if pair[key] is not None]
        sensitivity[key + "_median"] = median(values) if values else None
    result = dict(groups=groups, accounting=accounting, cases=cases,
                  supplemental_complete_transport=dict(pairs=unaffected_pairs, **sensitivity))
    runner.save(HERE / "diagnostics.json", result)
    runner.save(HERE / "TRACE-HASHES.json", manifests)
    print(json.dumps({"groups": groups, "accounting": accounting}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    analyze()
