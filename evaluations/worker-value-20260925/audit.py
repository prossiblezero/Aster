"""Recompute delegation comparisons from trace and usage, without model calls."""

import argparse
import json
from collections import Counter
from pathlib import Path

import tiktoken

ENCODER = tiktoken.get_encoding("cl100k_base")
READS = {"list_files", "read_file", "search", "run_shell", "observation_read"}


def read(path):
    return json.loads(path.read_text())


def summarize(folder):
    rows = []
    freeze = read(folder / "freeze.json")
    for original in read(folder / "results.json"):
        row = {
            k: original[k]
            for k in [
                "name",
                "task",
                "arm",
                "repetition",
                "phase",
                "stop_reason",
                "error",
                "passed",
                "grade",
                "seconds",
                "usage",
                "model_calls",
                "http_attempts",
                "http_with_total_usage",
                "workers_started",
                "workers_completed",
                "coordination_calls",
                "response_models",
                "diagnostic_flags",
                "source_unchanged",
                "docs_unchanged",
                "usage_matches",
            ]
        }
        launch = read(folder / "runs" / row["name"] / "launch.json")
        row.update(
            batch=folder.name,
            source=launch["source"],
            driver_commit=freeze.get("driver_commit"),
            resolved_source=(
                freeze["driver_commit"] if launch["source"] == "HEAD" else launch["source"]
            ),
        )
        ledger = read(folder / "runs" / row["name"] / "request-ledger.json")
        assert dict(sum((Counter(q["usage"]) for q in ledger), Counter())) == row["usage"]
        assert all(q["status"] != "pending" for q in ledger)
        assert row["source_unchanged"] and row["docs_unchanged"] and row["usage_matches"]
        row["tool_counts"] = dict(Counter(t["name"] for t in original["tools"]))
        row["unsuccessful_tools"] = dict(
            Counter(
                t["reason"] or t["status"] for t in original["tools"] if t["status"] != "success"
            )
        )
        sweep = sorted(
            x
            for q in ledger
            for x in [(q["started_seconds"], 1), (q["started_seconds"] + q["seconds"], -1)]
        )
        active = peak = 0
        for _, change in sweep:
            active += change
            peak = max(peak, active)
        row["max_overlapping_model_calls"] = peak
        row["runs"] = []
        for run in original["runs"]:
            workspace = folder / "workspaces" / row["name"]
            if not workspace.exists():
                workspace = Path(original["workspace"])
            root = workspace / ".aster/runs" / run["run_id"]
            events = [json.loads(line) for line in (root / "trace.jsonl").read_text().splitlines()]
            checkpoint = read(root / "checkpoint.json")
            if run["parent_run_id"]:
                assert checkpoint["runtime_identity"]["read_only"]
                assert not run["changed_paths"]
            results = [e for e in events if e["event"] == "tool_result"]
            observations = [e for e in results if e["call"]["name"] in READS]
            measurements = {
                "role": "child" if run["parent_run_id"] else "parent",
                "phase": run["phase"],
                "stop_reason": run["stop_reason"],
                "turns": run["turn"],
                "steps": run["step"],
                "usage": run["usage"],
                "direct_investigation_calls": len(observations),
                "raw_observation_tokens": sum(
                    len(ENCODER.encode(e["outcome"]["content"], disallowed_special=()))
                    for e in observations
                ),
                "max_estimated_request_tokens": max(
                    (e["estimated_tokens"] for e in events if e["event"] == "context_built"),
                    default=0,
                ),
                "max_reported_input_tokens": max(
                    (
                        e["usage"].get("input_tokens", 0)
                        for e in events
                        if e["event"] == "provider_attempt"
                    ),
                    default=0,
                ),
                "tool_counts": dict(Counter(e["call"]["name"] for e in results)),
                "observed_paths": sorted({
                    e["call"]["arguments"]["path"] for e in results
                    if e["call"]["name"] == "read_file"
                    and e["outcome"]["status"] == "success"
                }),
                "status_arguments": [
                    e["call"]["arguments"] for e in results if e["call"]["name"] == "worker_status"
                ],
            }
            row["runs"].append(measurements)
        assert sum(r["role"] == "parent" for r in row["runs"]) == 1
        rows.append(row)
    return rows


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("folders", type=Path, nargs="+")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rows = [r for folder in args.folders for r in summarize(folder)]
    args.output.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n")
    for row in rows:
        p = next(r for r in row["runs"] if r["role"] == "parent")
        print(
            json.dumps(
                {
                    "batch": row["batch"],
                    "name": row["name"],
                    "passed": row["passed"],
                    "tokens": row["usage"].get("total_tokens"),
                    "seconds": round(row["seconds"], 1),
                    "calls": row["model_calls"],
                    "tools": sum(row["tool_counts"].values()),
                    "parent_turns": p["turns"],
                    "parent_input": p["usage"].get("input_tokens"),
                    "parent_peak_input": p["max_reported_input_tokens"],
                    "parent_observations": p["raw_observation_tokens"],
                    "parent_reads": p["direct_investigation_calls"],
                },
                ensure_ascii=False,
            )
        )
