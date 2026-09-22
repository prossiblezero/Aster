"""Recompute comparisons from saved results and inspect actual requests, without API calls."""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
from collections import Counter
from pathlib import Path


def read(path):
    return json.loads(path.read_text())


def fraction_change(before, after):
    return (after / before - 1) * 100 if before else None


def paired(rows, left, right, *, independent=False):
    selected = [
        r
        for r in rows
        if r["family"] == "worker" and (not independent or r["category"] == "independent")
    ]
    keys = sorted({r["task"] for r in selected})
    pairs = []
    for task in keys:
        a = next(r for r in selected if r["task"] == task and r["arm"] == left)
        b = next(r for r in selected if r["task"] == task and r["arm"] == right)
        pairs.append(
            {
                "task": task,
                "both_passed": a["passed"] and b["passed"],
                "seconds": [a["seconds"], b["seconds"]],
                "tokens": [a["usage"].get("total_tokens"), b["usage"].get("total_tokens")],
                "time_change_pct": fraction_change(a["seconds"], b["seconds"]),
                "worker_count": b["workers_started"],
            }
        )
    common = [p for p in pairs if p["both_passed"]]
    return {
        "pairs": pairs,
        "both_passed": len(common),
        "median_paired_time_change_pct": statistics.median(p["time_change_pct"] for p in common)
        if common
        else None,
        "common_success_seconds": [sum(p["seconds"][i] for p in common) for i in (0, 1)],
        "common_success_tokens": [sum(p["tokens"][i] for p in common) for i in (0, 1)],
    }


def audit(output):
    rows = read(output / "results.json")
    expected = {
        ("worker", t, a, None)
        for t in [
            "csv-ledger",
            "access-report",
            "note-export",
            "artifact-index",
            "dependent-export",
            "shared-settings",
        ]
        for a in ["solo", "workers"]
    }
    expected |= {
        ("memory", t, a, s)
        for t in ["parcel", "agenda"]
        for a in ["off", "basic", "maintained"]
        for s in range(3)
    }
    expected |= {("learning", t, "shared", None) for t in ["parcel", "agenda"]}
    actual = [(r["family"], r["task"], r["arm"], r.get("stage")) for r in rows]
    assert len(actual) == len(set(actual)) == 32 and set(actual) == expected
    frozen = read(output / "freeze.json")
    here = Path(__file__).resolve().parent
    for name, digest in frozen["driver"].items():
        assert hashlib.sha256((here / name).read_bytes()).hexdigest() == digest
    assert all(r["source_unchanged"] and r["docs_unchanged"] and r["usage_matches"] for r in rows)
    attempts = []
    per_run = []
    scopes = []
    memory = []
    for row in rows:
        folder = output / "runs" / row["name"]
        ledger = read(folder / "request-ledger.json")
        assert all(r["status"] in {"completed", "failed"} for r in ledger)
        known = Counter()
        for request in ledger:
            known.update(request["usage"])
            attempts.extend({"name": row["name"], **a} for a in request["attempts"])
        assert dict(known) == row["usage"]
        root = Path(row["workspace"])
        parent = [n for n in row["runs"] if not n["parent_run_id"]]
        children = [n for n in row["runs"] if n["parent_run_id"]]
        assert len(parent) == 1
        for child in children:
            checkpoint = read(root / ".aster/runs" / child["run_id"] / "checkpoint.json")
            scope = checkpoint["runtime_identity"]["write_scope"]
            writes = sorted(
                {
                    p
                    for t in row["tools"]
                    if t["run_id"] == child["run_id"]
                    for p in t["changed_paths"]
                }
            )
            escaped = [
                p
                for p in writes
                if scope is not None
                and not any(p == s or p.startswith(s.rstrip("/") + "/") for s in scope)
            ]
            scopes.append(
                {
                    "name": row["name"],
                    "child": child["run_id"],
                    "scope": scope,
                    "written_paths": writes,
                    "outside_scope": escaped,
                }
            )
        # Actual overlaps of complete() spans, including wait, HTTP retries and streaming.
        sweep = []
        for request in ledger:
            sweep.extend(
                [
                    (request["started_seconds"], 1),
                    (request["started_seconds"] + request["seconds"], -1),
                ]
            )
        active = maximum = 0
        for _, delta in sorted(sweep):
            active += delta
            maximum = max(maximum, active)
        per_run.append(
            {
                "name": row["name"],
                "parent_usage": parent[0]["usage"],
                "children_usage": dict(sum((Counter(n["usage"]) for n in children), Counter())),
                "max_overlapping_model_calls": maximum,
            }
        )
        if row["family"] == "memory":
            before = read(folder / "memory-before.json")["lessons"]
            recalled = [e for e in row["memory_events"] if e["event"] == "memory_recalled"]
            first_ids = recalled[0]["ids"] if recalled else []
            wire_ids = [m["id"] for m in before if "docs/wire.md" in m.get("sources", {})]
            first_files = sorted(
                (root / ".aster/runs" / parent[0]["run_id"] / "requests").glob("0001-*.json")
            )
            assert len(first_files) == 1
            first = read(first_files[0])["payload"]
            contexts = [
                m.get("content", "")
                for m in first.get("input", [])
                if isinstance(m.get("content"), str) and m["content"].startswith("Context sections")
            ]
            # Check the memory section, not the prompt or historical tool observations.
            memory_section = "\n".join(
                c.split("memory:\n", 1)[1].split("\nworking_memory:", 1)[0]
                for c in contexts
                if "memory:\n" in c
            )
            by_id = {m["id"]: m for m in before}
            memory.append(
                {
                    "name": row["name"],
                    "stage": row["stage"],
                    "arm": row["arm"],
                    "first_recalled_ids": first_ids,
                    "first_recalled_text": [by_id[i]["text"] for i in first_ids if i in by_id],
                    "stale_wire_ids": wire_ids if row["stage"] == 2 else [],
                    "stale_wire_ids_recalled": sorted(set(wire_ids) & set(first_ids))
                    if row["stage"] == 2
                    else [],
                    "first_memory_section": memory_section,
                    "feedback_before": [
                        {
                            k: m.get(k)
                            for k in ["id", "successful_uses", "failed_uses", "inactive_reason"]
                        }
                        for m in before
                    ],
                    "feedback_after": [
                        {
                            k: m.get(k)
                            for k in ["id", "successful_uses", "failed_uses", "inactive_reason"]
                        }
                        for m in row["lessons"]
                    ],
                    "dream": [e for e in row["memory_events"] if e["event"] == "memory_dream"],
                    "source_lookup_tools": [
                        t for t in row["tools"] if t["name"] in {"read_file", "search", "run_shell"}
                    ],
                }
            )
    report = {
        "complete_matrix": True,
        "runs": len(rows),
        "source_unchanged": True,
        "all_runtime_usage_matches_ledger": True,
        "http_attempts": len(attempts),
        "http_with_total_usage": sum("total_tokens" in a["usage"] for a in attempts),
        "http_classifications": dict(Counter(a["classification"] for a in attempts)),
        "response_models": dict(Counter(a["response_model"] for a in attempts)),
        "flags": dict(Counter(f for a in attempts for f in a["flags"])),
        "whole_tree_usage": dict(sum((Counter(r["usage"]) for r in rows), Counter())),
        "runs_accounting": per_run,
        "child_write_scopes": scopes,
        "memory": memory,
        "worker_pairs": paired(rows, "solo", "workers"),
        "worker_independent_pairs": paired(rows, "solo", "workers", independent=True),
    }
    (output / "audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {
                k: v
                for k, v in report.items()
                if k
                not in {
                    "runs_accounting",
                    "child_write_scopes",
                    "memory",
                    "worker_pairs",
                    "worker_independent_pairs",
                }
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    audit(parser.parse_args().output)
