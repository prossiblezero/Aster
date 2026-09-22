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


def audit(output, directed=None, public=None):
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
    revisions = read(output / "regrade.json") if (output / "regrade.json").exists() else None
    if revisions:
        by_name = {r["name"]: r for r in revisions["results"]}
        for row in rows:
            if row["name"] in by_name:
                row["original_passed"], row["original_grade"] = row["passed"], row["grade"]
                row["grade"] = by_name[row["name"]]["grade"]
                row["passed"] = (
                    row["phase"] == "completed" and row["docs_unchanged"] and row["grade"]["passed"]
                )
    frozen = read(output / "freeze.json")
    here = Path(__file__).resolve().parent
    for name, digest in frozen["driver"].items():
        assert hashlib.sha256((here / name).read_bytes()).hexdigest() == digest
    source = here.parents[1] / "src/aster"
    assert {
        str(p.relative_to(source)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in source.rglob("*.py")
        if "__pycache__" not in p.parts
    } == frozen["source"]
    directed_rows = []
    if directed is not None:
        directed_rows = read(directed / "results.json")
        assert len(directed_rows) == 4
        assert {(r["task"], r["arm"]) for r in directed_rows} == {
            (t, a) for t in ["csv-ledger", "access-report"] for a in ["solo", "directed"]
        }
        for name, digest in read(directed / "freeze.json")["driver"].items():
            assert hashlib.sha256((here / name).read_bytes()).hexdigest() == digest
        rows.extend(directed_rows)
    assert all(r["source_unchanged"] and r["docs_unchanged"] and r["usage_matches"] for r in rows)
    attempts = []
    per_run = []
    scopes = []
    memory = []
    for row in rows:
        folder = (directed if row["family"] == "directed" else output) / "runs" / row["name"]
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
        first_files = sorted(
            (root / ".aster/runs" / parent[0]["run_id"] / "requests").glob("0001-*.json")
        )
        assert len(first_files) == 1
        first = read(first_files[0])["payload"]
        schemas = {t["name"]: t for t in first["tools"]}
        loadable = schemas["tool_load"]["parameters"]["properties"]["names"]["items"]["enum"]
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
                    "phase": child["phase"],
                    "stop_reason": child["stop_reason"],
                    "steps": child["step"],
                    "turns": child["turn"],
                    "usage": child["usage"],
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
                "worker_schema_initially_loaded": "worker_start" in schemas,
                "worker_start_loadable": "worker_start" in loadable,
                "worker_load_calls": [
                    t
                    for t in row["tools"]
                    if t["name"] == "tool_load"
                    and any(n.startswith("worker_") for n in t["arguments"].get("names", []))
                ],
            }
        )
        if row["family"] == "memory":
            before = read(folder / "memory-before.json")["lessons"]
            recalled = [e for e in row["memory_events"] if e["event"] == "memory_recalled"]
            first_ids = recalled[0]["ids"] if recalled else []
            wire_ids = [
                m["id"]
                for m in before
                if any(p.lstrip("./") == "docs/wire.md" for p in m.get("sources", {}))
            ]
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
            assert all(by_id[i]["text"] in memory_section for i in first_ids)
            recalled_sources = sorted(
                {p.lstrip("./") for i in first_ids for p in by_id[i].get("sources", {})}
            )
            # Post hoc diagnosis of retrieval coverage, not a preregistered outcome.
            contracts = {
                0: {"wire", "identity", "order", "weight" if row["task"] == "parcel" else "time"},
                1: {"identity", "weight" if row["task"] == "parcel" else "time"},
                2: {"wire"},
            }[row["stage"]]
            required = sorted(f"docs/{p}.md" for p in contracts)
            memory.append(
                {
                    "name": row["name"],
                    "stage": row["stage"],
                    "arm": row["arm"],
                    "first_recalled_ids": first_ids,
                    "first_recalled_sources": recalled_sources,
                    "posthoc_required_sources": required,
                    "posthoc_covered_sources": sorted(set(required) & set(recalled_sources)),
                    "all_recalled_id_sets": sorted({tuple(e["ids"]) for e in recalled}),
                    "first_recalled_text": [by_id[i]["text"] for i in first_ids if i in by_id],
                    "stale_wire_ids": wire_ids if row["stage"] == 2 else [],
                    "stale_wire_ids_recalled": sorted(set(wire_ids) & set(first_ids))
                    if row["stage"] == 2
                    else [],
                    "stale_wire_text_in_first_memory": [
                        i for i in wire_ids if by_id[i]["text"] in memory_section
                    ]
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
        "grading_revisions": revisions,
        "directed_pairs": paired(
            [{**r, "family": "worker"} for r in directed_rows], "solo", "directed"
        ),
        "groups": {
            f"{family}/{arm}": {
                "runs": len(group),
                "passed": sum(r["passed"] for r in group),
                "code_passed": sum(bool(r["grade"] and r["grade"]["passed"]) for r in group),
                "usage": dict(sum((Counter(r["usage"]) for r in group), Counter())),
                **{
                    k: sum(r[k] for r in group)
                    for k in [
                        "seconds",
                        "model_calls",
                        "http_attempts",
                        "http_with_total_usage",
                        "doc_read_calls",
                        "read_calls",
                        "search_calls",
                        "shell_calls",
                        "workers_started",
                        "workers_completed",
                        "coordination_calls",
                        "memory_store_attempts",
                        "memory_store_successes",
                    ]
                },
            }
            for family, arm in sorted({(r["family"], r["arm"]) for r in rows})
            if (group := [r for r in rows if r["family"] == family and r["arm"] == arm])
        },
    }
    (output / "analysis-results.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2) + "\n"
    )
    (output / "audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    if public:
        # Publish measured outcomes without prompts, final answers, or private reasoning.
        keys = [
            "name",
            "family",
            "task",
            "category",
            "arm",
            "stage",
            "phase",
            "stop_reason",
            "error",
            "passed",
            "original_passed",
            "seconds",
            "usage",
            "model_calls",
            "http_attempts",
            "http_with_total_usage",
            "workers_started",
            "workers_completed",
            "coordination_calls",
            "scope_conflicts",
            "doc_read_calls",
            "read_calls",
            "search_calls",
            "shell_calls",
            "memory_store_attempts",
            "memory_store_successes",
        ]
        published = {k: v for k, v in report.items() if k not in {"memory", "grading_revisions"}}
        published["attempts"] = [
            {
                **{k: r[k] for k in keys if k in r},
                "grade": {k: v for k, v in r["grade"].items() if k != "output"}
                if r["grade"]
                else None,
            }
            for r in rows
        ]
        published["memory"] = [
            {
                k: v
                for k, v in m.items()
                if k not in {"first_recalled_text", "first_memory_section", "source_lookup_tools"}
            }
            for m in memory
        ]
        published["main_freeze"] = frozen
        published["directed_freeze"] = read(directed / "freeze.json") if directed else None
        published["preflight"] = [
            {"id": c["id"], "passed": c["passed"]}
            for c in read(output.parent / "aster-worker-memory-preflight-20260922/preflight.json")
        ]
        published["grading_revision"] = "ERRATA.md; original_passed preserves initial decisions"
        public.write_text(json.dumps(published, ensure_ascii=False, indent=2) + "\n")
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
                    "grading_revisions",
                    "directed_pairs",
                }
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--directed", type=Path)
    parser.add_argument("--public", type=Path, help="Optional compact public evidence JSON")
    args = parser.parse_args()
    audit(args.output, args.directed, args.public)
