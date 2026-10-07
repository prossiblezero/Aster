"""Recompute the fixed ten-task comparison from completed episode artifacts."""

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def read(path):
    return json.loads(path.read_text())


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
    summary = {arm: summarize(arm) for arm in ("baseline", "sleep")}
    (HERE / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({arm: {key: value for key, value in values.items() if key != "details"}
                      for arm, values in summary.items()}, ensure_ascii=False, indent=2))
