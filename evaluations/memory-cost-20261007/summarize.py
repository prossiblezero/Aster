"""Recompute the fixed ten-task comparison from completed episode artifacts."""

import gzip
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def read(path):
    return json.loads(path.read_text())


def costs(arm):
    buckets = {name: {"attempts": 0, "known": 0, "input": 0, "cached": 0,
                      "uncached": 0, "output": 0, "with_cache": 0}
               for name in ("foreground", "maintenance")}
    for path in sorted((HERE / "results" / arm).glob("*/*.trace.jsonl.gz")):
        with gzip.open(path, "rt") as trace:
            for line in trace:
                event = json.loads(line)
                if (event["event"] not in {"provider_attempt", "memory_provider"}
                        or event.get("type") != "diagnostic"):
                    continue
                bucket = buckets["maintenance" if event["event"] == "memory_provider" else "foreground"]
                bucket["attempts"] += 1
                usage = event.get("usage", {})
                if not all(type(usage.get(key)) is int for key in
                           ("input_tokens", "cache_read_input_tokens", "output_tokens")):
                    continue
                inputs, cached, outputs = (usage[key] for key in
                                          ("input_tokens", "cache_read_input_tokens", "output_tokens"))
                assert 0 <= cached <= inputs
                bucket["known"] += 1
                bucket["input"] += inputs
                bucket["cached"] += cached
                bucket["uncached"] += inputs - cached
                bucket["output"] += outputs
                bucket["with_cache"] += cached > 0
    buckets["all"] = {key: sum(b[key] for b in buckets.values())
                      for key in buckets["foreground"]}
    for bucket in buckets.values():
        bucket["unknown"] = bucket["attempts"] - bucket["known"]
        bucket["cache_share"] = bucket["cached"] / bucket["input"] if bucket["input"] else None
    return buckets


def summarize(arm):
    rows = []
    learning, reuse = [], []
    for path in sorted((HERE / "results" / arm).glob("*/result.json")):
        result = read(path)
        tested = read(path.parent / "reuse.json")
        learned = [read(p) for p in sorted(path.parent.glob("learn-*.json"))]
        learning.extend(learned)
        reuse.append(tested)
        source_runs = {row["run_id"] for row in learned}
        sourced_ids = {item["id"] for item in tested["memory_after"]
                       if item.get("provenance", {}).get("run_id") in source_runs
                       or any(c.get("run_id") in source_runs
                              for c in item.get("provenance", {}).get("citations", []))}
        # A cancelled CLI drain can finish learning during the following task.
        # Attribute by source run rather than only the earlier memory snapshot.
        result["learning_source_recalled_ids"] = sorted(sourced_ids & set(tested["recalled_ids"]))
        result["learning_sources_saved_by_end"] = bool(sourced_ids)
        rows.append(result)
    assert len(rows) == 10, f"{arm}: only {len(rows)}/10 complete"
    episodes = [*learning, *reuse]
    maintenance = [event for row in episodes for event in row.get("maintenance_events", [])]
    receipts = [receipt for event in maintenance for receipt in event.get("receipts", [])]
    confirmed = sum(any(item["id"] == receipt.get("id") for item in row["memory_after"])
                    for row in episodes for event in row.get("maintenance_events", [])
                    for receipt in event.get("receipts", []) if receipt.get("action") != "deactivate")
    checks = [value for row in rows for value in row["grade"]["checks"].values()]
    attribution = read(HERE / "results" / arm / "10_feedback_isolation" / "reuse.json")
    before = {item["id"]: item for item in attribution["memory_before"]}
    feedback = sum(
        item["successful_uses"] + item["failed_uses"]
        - before[item["id"]]["successful_uses"] - before[item["id"]]["failed_uses"]
        for item in attribution["memory_after"] if item["id"] in before
    )
    return {
        "tasks": len(rows), "episodes": len(episodes),
        "cost_components": costs(arm),
        "pending_maintenance_at_end": sum(row["pending_maintenance"] for row in reuse),
        "unchanged_evidence_skips": sum(event.get("reason") == "unchanged_evidence" for event in maintenance),
        "inline_test_calls": sum(bool(call["arguments"].get("code")) for row in episodes
                                 for call in row["tools"] if call["name"] == "run_tests"),
        "all_tool_calls": sum(len(row["tools"]) for row in episodes),
        "failed_tool_calls": sum(call["status"] != "success" for row in episodes for call in row["tools"]),
        "maintenance_calls": sum(row.get("memory_usage", {}).get("calls", 0) for row in episodes),
        "maintenance_tokens_reported": sum(row.get("memory_usage", {}).get("total_tokens", 0) for row in episodes),
        "maintenance_usage_missing": sum(row.get("memory_usage", {}).get("unknown_usage", 0) for row in episodes),
        "maintenance_committed_operations": len(receipts),
        "maintenance_store_replace_receipts": sum(r.get("action") != "deactivate" for r in receipts),
        "maintenance_store_replace_confirmed": confirmed,
        "learning_maintenance_tokens": sum(row.get("memory_usage", {}).get("total_tokens", 0) for row in learning),
        "learning_saved_tasks": sum(row["learned_memories"] > 0 for row in rows),
        "learning_sources_saved_by_end": sum(row["learning_sources_saved_by_end"] for row in rows),
        "foreground_seconds": sum(row["foreground_seconds"] for row in episodes),
        "total_episode_seconds": sum(row["seconds"] for row in episodes),
        "temporary_label_memories": sum("TEMP-DEMO-742" in item["text"] for row in reuse for item in row["memory_after"]),
        "delivered_finals": sum(bool(row["final"]) for row in reuse),
        "task_success": sum(row["passed"] for row in rows),
        "pipeline_success": sum(row["passed"] and row["learning_passed"] for row in rows),
        "learning_completed": sum(row["phase"] == "completed" for row in learning),
        "learning_episodes": len(learning),
        "functional_success": sum(row["grade"]["passed"] for row in rows),
        "constraint_passed": sum(value["passed"] for value in checks),
        "constraint_total": 30,
        "learning_tokens_reported": sum(row["total_tokens"] for row in learning if row["total_tokens"] is not None),
        "reuse_tokens_reported": sum(row["total_tokens"] for row in reuse if row["total_tokens"] is not None),
        "total_tokens_reported": sum(row["total_tokens"] for row in episodes if row["total_tokens"] is not None),
        "usage_complete": all(row["usage_coverage"]["total_tokens"]["complete"] for row in episodes),
        "requests_with_total_usage": sum(row["usage_coverage"]["total_tokens"]["reported_requests"] or 0 for row in episodes),
        "model_requests": sum(row["model_requests"] for row in episodes),
        "provider_attempts": sum(row["provider_attempts"] for row in episodes),
        "provider_attempts_with_total": sum(row["provider_attempts_with_total"] for row in episodes),
        "provider_usage_complete": all(row["provider_attempts"] == row["provider_attempts_with_total"] for row in episodes),
        "reuse_tool_calls": sum(len(row["tools"]) for row in reuse),
        "reuse_document_reads": sum(call["name"] == "read_file" and (
            call["arguments"].get("path", "").startswith("docs/")
            or call["arguments"].get("path", "").endswith(".md")
        ) for row in reuse for call in row["tools"]),
        "reuse_failed_tests": sum(row["reuse_failed_tests"] for row in rows),
        "tasks_recalling_learning": sum(bool(row["learning_source_recalled_ids"]) for row in rows),
        "successful_tasks_recalling_learning": sum(row["passed"] and bool(row["learning_source_recalled_ids"]) for row in rows),
        "user_convention_success": sum(row["passed"] for row in rows if row["focus"] in {
            "user_convention", "user_correction", "scope_separation",
        }),
        "user_convention_functional_success": sum(row["grade"]["passed"] for row in rows if row["focus"] in {
            "user_convention", "user_correction", "scope_separation",
        }),
        "user_convention_tasks": sum(row["focus"] in {"user_convention", "user_correction", "scope_separation"} for row in rows),
        "unrelated_memory_feedback": feedback,
        "details": [{key: row[key] for key in ("id", "passed", "grade", "reuse_tokens", "learned_memories", "learning_source_recalled_ids")}
                    for row in rows],
    }


if __name__ == "__main__":
    summary = {arm: summarize(arm) for arm in ("baseline", "cost")}
    sensitivity = []
    for cache_weight in (0.1, 0.25, 1):
        for output_weight in (1, 4, 8):
            row = {"cache_weight": cache_weight, "output_weight": output_weight}
            for arm in ("baseline", "cost"):
                parts = summary[arm]["cost_components"]["all"]
                row[arm] = round(parts["uncached"] + cache_weight * parts["cached"]
                                 + output_weight * parts["output"], 2)
            row["change_percent"] = round(100 * (row["cost"] / row["baseline"] - 1), 2)
            sensitivity.append(row)
    summary["price_sensitivity_not_actual_bill"] = sensitivity
    (HERE / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({arm: {key: value for key, value in summary[arm].items() if key != "details"}
                      for arm in ("baseline", "cost")}, ensure_ascii=False, indent=2))
