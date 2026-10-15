"""Read-only pilot comparison, preserving errors and incomplete usage receipts."""
import json
import tarfile
from datetime import datetime

import evaluate as runner


def trace(root):
    before, after, context, provider = 0.0, 0.0, None, None
    with tarfile.open(root / ".aster.tar") as archive:
        events = next(m for m in archive.getmembers() if m.name.endswith("/trace.jsonl"))
        checkpoint = next(m for m in archive.getmembers() if m.name.endswith("/checkpoint.json"))
        for line in archive.extractfile(events):
            event = json.loads(line)
            now = datetime.fromisoformat(event["time"]).timestamp()
            if event["event"] == "context_built":
                context = now
            elif event["event"] == "model_stream" and event.get("type") == "begin" and context is not None:
                before += now - context
                context = None
            elif event["event"] == "provider_attempt":
                provider = now
            elif event["event"] == "model_reply" and provider is not None:
                after += now - provider
                provider = None
    payloads = [runner.load(p) for p in (root / "provider").glob("*.request.json")]
    return {"checkpoint_bytes": checkpoint.size, "before_request_gap_seconds": round(before, 3),
            "after_reply_gap_seconds": round(after, 3), "requests": len(payloads),
            "requests_with_cache_key": sum(bool(p.get("prompt_cache_key")) for p in payloads)}


def main():
    result = {"phases": {}, "trace_limit": "Gaps include checkpoint work and other local work, not exclusively persistence."}
    incomplete_grades = []
    for phase in ("pilot-baseline", "pilot-candidate"):
        runner.configure(phase)
        runner.check_sources()
        rows = runner.load(runner.HERE / "results.json")
        for row in rows:
            if row["kind"] == "repository" and not (row.get("grade") or {}).get("tests_status"):
                incomplete_grades.append({"phase": phase, "task": row["task"], "grade": row.get("grade")})
        roots = [runner.PRIVATE / "runs" / ("aster-" + r["task"]) for r in rows]
        traces = {r["task"]: trace(root) for r, root in zip(rows, roots)}
        wall = max((p / "result.json").stat().st_mtime for p in roots) - min(
            runner.load(p / "launch.json")["started"] for p in roots)
        result["phases"][phase] = {"summary": runner.load(runner.HERE / "summary.json"),
                                   "wall_seconds": round(wall, 3), "traces": traces}
    result["gate"] = runner.load(runner.ROOT / "gate.json")
    # The historical scorer can mark infra_failure=False while reporting no_tests_collected.
    # Preserve its raw output, but never interpret that as completed independent testing.
    result["grading_audit"] = {"incomplete_test_execution": incomplete_grades,
                               "all_tests_executed": not incomplete_grades}
    result["effective_proceed_to_full"] = result["gate"]["proceed_to_full"] and not incomplete_grades
    probe = runner.load(runner.ROOT / "cache-probe.json")
    result["development_total"] = {
        "task_attempts": 8, "probe_attempts": len(probe["requests"]),
        "requests": sum(p["summary"]["requests"] for p in result["phases"].values()) + len(probe["requests"]),
        "missing_usage": sum(p["summary"]["missing_usage"] for p in result["phases"].values()),
        "reported_total_tokens": sum(p["summary"]["total_tokens"] for p in result["phases"].values())
        + sum(p.get("usage", {}).get("total_tokens", 0) for p in probe["requests"]),
    }
    runner.save(runner.ROOT / "comparison.json", result)
    print(json.dumps({"development_total": result["development_total"],
                      "proceed_to_full": result["gate"]["proceed_to_full"]}, indent=2))


if __name__ == "__main__":
    main()
