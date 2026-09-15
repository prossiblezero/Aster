"""Independently validate saved attempts, real request budgets and trace chains."""

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from statistics import mean

from aster.context import ContextBuilder, history_groups
from aster.workspace import Workspace


def validate(report_path):
    report = json.loads(report_path.read_text())
    rows = report["rows"]

    def key(row):
        return row["task_id"], row["variant"], row["attempt"]

    assert Counter(map(key, rows)) == Counter(map(key, report["manifest"]))
    assert len(set(map(key, rows))) == len(rows)
    verified, requests_checked, traces_checked = [], 0, 0
    for row in rows:
        if not row.get("trace_path"):
            assert not row["passed"]
            continue
        path = Path(row["trace_path"])
        destination = path.parent.parent
        checkpoint = json.loads((path.parent / "checkpoint.json").read_text())
        assert Workspace(destination / "candidate").fingerprint() == checkpoint["workspace_hash"]
        previous = ""
        events = []
        for line in path.read_text().splitlines():
            event = json.loads(line)
            expected_hash = event.pop("hash")
            assert event["previous"] == previous
            assert (
                hashlib.sha256(
                    json.dumps(event, sort_keys=True, ensure_ascii=False).encode()
                ).hexdigest()
                == expected_hash
            )
            previous = expected_hash
            events.append(event)
        traces_checked += 1
        handoff = next(event["seq"] for event in events if event["event"] == "benchmark_handoff")
        contexts = [
            event
            for event in events
            if event["event"] == "context_built" and event["seq"] > handoff
        ]
        files = sorted((destination / "requests").glob("request-*.json"))
        assert len(files) == row["model_requests"] == len(contexts)
        counter = ContextBuilder(1_000_000, report["settings"]["output_tokens"])
        for file, event in zip(files, contexts, strict=True):
            request = json.loads(file.read_text())
            payload = request["payload"]
            rendered = json.dumps(payload, ensure_ascii=False)
            assert hashlib.sha256(rendered.encode()).hexdigest() == request["sha256"]
            assert (
                counter.count(payload) == request["estimated_tokens"] == event["estimated_tokens"]
            )
            assert request["estimated_tokens"] <= event["budget"]
            history_groups(payload["input"])
            if row["variant"] == "silent":
                assert "Recovery required (" not in rendered
            requests_checked += 1
        assert row["history_compactions"] == sum(
            event["removed_messages"] > 0 for event in contexts
        )
        if row["passed"]:
            assert row["verifier"]["passed"] and checkpoint["state"]["phase"] == "completed"
        verified.append(
            {
                "task_id": row["task_id"],
                "variant": row["variant"],
                "attempt": row["attempt"],
                "requests": len(files),
                "verified": True,
            }
        )
    summary = {}
    for experiment in ("context", "recovery"):
        subset = [row for row in rows if row["experiment"] == experiment]
        for category in sorted({row["category"] for row in subset} | {"primary"}):
            categories = [
                row
                for row in subset
                if (
                    row["category"] != "loss_control"
                    if category == "primary"
                    else row["category"] == category
                )
            ]
            for variant in sorted({row["variant"] for row in categories}):
                group = [row for row in categories if row["variant"] == variant]
                measured = [row for row in group if row.get("first_request")]
                summary[f"{experiment}/{category}/{variant}"] = {
                    "scenarios": len({row["task_id"] for row in group}),
                    "attempts": len(group),
                    "completed": sum(row["passed"] for row in group),
                    "functional_passed": sum(
                        row.get("verifier", {}).get("passed", False) for row in group
                    ),
                    "stops": dict(Counter(row["stop_reason"] for row in group)),
                    "mean_first_input": mean(
                        row["first_request"]["estimated_tokens"] for row in measured
                    )
                    if measured
                    else None,
                    "first_requests_checked": len(measured),
                    "first_requests_with_all_history_evidence": sum(
                        all(row["first_request"]["evidence_present"]) for row in measured
                    )
                    if experiment == "context"
                    else None,
                    "mean_untrimmed_input": mean(
                        row["setup"]["untrimmed"]["estimated_tokens"] for row in measured
                    )
                    if experiment == "context" and measured
                    else None,
                    "attempts_with_history_compaction": sum(
                        row.get("history_compactions", 0) > 0 for row in group
                    ),
                    "evidence_read_calls": sum(row["evidence_read_calls"] for row in group),
                    "mean_tokens_all": mean(row["total_tokens"] for row in group),
                    "mean_steps_all": mean(row["autonomous_steps"] for row in group),
                    "mean_seconds_all": mean(row["seconds"] for row in group),
                }
    primary_pairs = {}
    for name, group in report["pairs"].items():
        experiment, category, left, right = name.split("/")
        if category == "loss_control":
            continue
        primary_pairs.setdefault(f"{experiment}/{left}/{right}", []).append(group)
    for name, groups in primary_pairs.items():
        combined = {
            field: sum(group[field] for group in groups)
            for field in ("pairs", "both_pass", "left_only", "right_only", "neither")
        }
        for field in groups[0]:
            if field.startswith("common_success_"):
                combined[field] = (
                    [
                        sum(
                            group[field][side] * group["both_pass"]
                            for group in groups
                            if group["both_pass"]
                        )
                        / combined["both_pass"]
                        for side in (0, 1)
                    ]
                    if combined["both_pass"]
                    else None
                )
        primary_pairs[name] = combined
    return {
        "source_report": str(report_path),
        "source_report_sha256": hashlib.sha256(report_path.read_bytes()).hexdigest(),
        "attempts": len(rows),
        "traces_checked": traces_checked,
        "requests_checked": requests_checked,
        "manifest_complete": True,
        "summary": summary,
        "primary_pairs": primary_pairs,
        "rows": verified,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    result = validate(args.report)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {
                key: result[key]
                for key in ("attempts", "traces_checked", "requests_checked", "manifest_complete")
            }
        )
    )
