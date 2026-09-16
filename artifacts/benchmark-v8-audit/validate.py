"""Recheck saved context requests and traces; does not call a model or change reports.

Run with the evaluated worktree's src and scripts on PYTHONPATH.
"""

import argparse
import hashlib
import json
from pathlib import Path

from compare_context_runs import index

from aster.context import ContextBuilder
from aster.evidence import Redactor
from aster.provider import history_groups


def digest(value, *, sort_keys=False):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=sort_keys).encode()
    ).hexdigest()


def validate(report_path):
    report = json.loads(report_path.read_text())
    rows = list(index(report).values())
    checked = {"traces": 0, "model_requests": 0, "context_builds": 0, "archives": 0}
    policies = set()
    builder = ContextBuilder(1_000_000, report["settings"]["output_tokens"])
    for row in rows:
        trace_path = Path(row["trace_path"])
        events = [json.loads(line) for line in trace_path.read_text().splitlines()]
        previous = ""
        for seq, event in enumerate(events, 1):
            assert event["seq"] == seq and event["previous"] == previous, trace_path
            unhashed = {key: value for key, value in event.items() if key != "hash"}
            assert digest(unhashed, sort_keys=True) == event["hash"], trace_path
            previous = event["hash"]
        checked["traces"] += 1
        contexts = [event for event in events if event["event"] == "context_built"]
        requests = sorted((trace_path.parent.parent / "requests").glob("request-*.json"))
        assert len(requests) == row["model_requests"] == len(contexts), trace_path
        for path, event in zip(requests, contexts, strict=True):
            saved = json.loads(path.read_text())
            payload = saved["payload"]
            assert digest(payload) == saved["sha256"], path
            assert (
                builder.count(payload) == saved["estimated_tokens"] == event["estimated_tokens"]
            ), path
            assert event["estimated_tokens"] <= event["budget"], path
            assert event["budget"] == row["setup"]["selected"]["budget"], path
            history_groups(payload["input"])
            runtime_path = trace_path.parent / "requests" / Path(event["request_artifact"]).name
            runtime_saved = json.loads(runtime_path.read_text())
            assert digest(runtime_saved["payload"], sort_keys=True) == event["request_sha256"], (
                runtime_path
            )
            assert runtime_saved["payload"] == Redactor().value(payload, mask_fields=False), path
            assert runtime_saved["policy_version"] == event["policy_version"], path
            policies.add(event["policy_version"])
            checked["model_requests"] += 1
            checked["context_builds"] += 1
        referenced = {key for event in contexts for key in event["archive_hashes"]}
        for observation_id in referenced:
            observation_path = trace_path.parent / "observations" / (observation_id + ".json")
            observation = json.loads(observation_path.read_text())
            assert digest(observation, sort_keys=True) == observation_id, observation_path
            checked["archives"] += 1
    return {
        "source_report": str(report_path),
        "source_report_sha256": hashlib.sha256(report_path.read_bytes()).hexdigest(),
        "source": report["source"],
        "attempts": len(rows),
        "manifest_complete": True,
        "policies": sorted(policies),
        **checked,
        "pairing_valid_rate": 1.0 if checked["model_requests"] else None,
        "input_budget_compliance_rate": 1.0 if checked["model_requests"] else None,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    results = [validate(path.resolve()) for path in args.reports]
    args.output.write_text(json.dumps({"reports": results}, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {
                "reports": len(results),
                "attempts": sum(r["attempts"] for r in results),
                "requests": sum(r["model_requests"] for r in results),
            },
            ensure_ascii=False,
        )
    )
