"""Recompute the three acceptance groups from preserved results; no model calls."""
import hashlib
import json
import statistics
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
PUBLIC = REPO / "evaluations/harness-v12-20261017/runs"
PRIVATE = REPO / "artifacts/private/harness-v12-20261017"
RUNS = {
    "benchmark": "fin-fresh30b-gpt-v15",
    "default": "gate-default-gpt-1020",
    "off": "gate-off-gpt-1020",
}
LABELS = {"benchmark": "严格验收（历史）", "default": "产品默认", "off": "关闭门禁"}


def load(path):
    return json.loads(path.read_text())


def totals(rows):
    result = {key: sum(row[key] for row in rows) for key in (
        "requests", "input_tokens", "cached_tokens", "output_tokens", "missing_usage",
        "request_errors", "seconds", "model_seconds", "passed", "normal_delivery_and_passed",
    )}
    for key in ("final_revisions", "turns", "steps"):
        result["recorded_" + key] = sum(row[key] for row in rows if row["state_available"])
    result["tasks"] = len(rows)
    result["completed"] = sum(row["phase"] == "completed" for row in rows)
    result["verified"] = sum(row["stop_reason"] == "verified" for row in rows)
    result["completed_with_warning"] = sum(row["stop_reason"] == "completed_with_warning" for row in rows)
    result["missing_states"] = sum(not row["state_available"] for row in rows)
    result["completed_but_failed"] = sum(row["phase"] == "completed" and not row["passed"] for row in rows)
    result["verified_but_failed"] = sum(row["stop_reason"] == "verified" and not row["passed"] for row in rows)
    result["timeouts"] = sum(row["timed_out"] for row in rows)
    result["uncached_tokens"] = result["input_tokens"] - result["cached_tokens"]
    result["cache_ratio"] = result["cached_tokens"] / result["input_tokens"] if result["input_tokens"] else None
    result["known_weighted_cost"] = (
        result["uncached_tokens"] * 1.25 + result["cached_tokens"] * .125 + result["output_tokens"] * 10
    ) / 1e6
    result["median_seconds"] = statistics.median(row["seconds"] for row in rows) if rows else None
    return result


def table(groups):
    lines = [
        "| 设置 | 独立通过 | 正常交付且通过 | 完成但独立失败 / completed | 请求 | 已知费用估算 | 缺失用量 | 任务耗时合计 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for mode, row in groups.items():
        n = row["tasks"]
        lines.append(
            f"| {LABELS[mode]} | {row['passed']}/{n} | {row['normal_delivery_and_passed']}/{n} | "
            f"{row['completed_but_failed']}/{row['completed']} | {row['requests']} | "
            f"{row['known_weighted_cost']:.6f} | {row['missing_usage']} | {row['seconds'] / 60:.1f} 分钟 |"
        )
    return "\n".join(lines)


def main():
    for name, expected in load(HERE / "preflight.json")["historical_file_hashes"].items():
        assert hashlib.sha256((REPO / name).read_bytes()).hexdigest() == expected, name
    manifests = {mode: load(PUBLIC / name / "manifest.json") for mode, name in RUNS.items()}
    baseline = manifests["benchmark"]
    groups = {}
    for mode, name in RUNS.items():
        manifest = manifests[mode]
        for key in ("aster_commit", "tasks", "product_hashes", "model", "protocol", "reasoning",
                    "seconds_per_task", "context_tokens", "output_tokens", "concurrency"):
            assert manifest[key] == baseline[key], (mode, key)
        assert manifest.get("acceptance", "benchmark") == mode
        rows = load(PUBLIC / name / "results.json")
        assert len(rows) == len(baseline["tasks"]) == 30
        assert {row["task"] for row in rows} == set(baseline["tasks"])
        assert all(row["graded"] for row in rows), name
        for row in rows:
            grade = row["grade"]
            assert not grade.get("infra_failure", False), (name, row["task"], "invalid grade")
            if row["kind"] != "repository" and grade.get("tests", 0) == 0:
                # A candidate that fails to compile is a valid failure, even with zero tests.
                assert not row["passed"], (name, row["task"], "zero-test pass")
            path = PRIVATE / name / "runs" / row["job"] / "aster-result.json"
            row["state_available"] = path.exists()
            state = load(path) if path.exists() else {}
            row["final_revisions"] = state.get("final_revisions")
            row["turns"] = state.get("turn")
            row["steps"] = state.get("step")
            row["task_requirements"] = {key: state.get("task", {}).get(key) for key in (
                "require_edit", "require_verification", "require_observation",
            )}
        groups[mode] = {row["task"]: row for row in rows}
    excluded = {}
    for task in baseline["tasks"]:
        reasons = {mode: {key: rows[task][key] for key in ("stop_reason", "request_errors")}
                   for mode, rows in groups.items()
                   if rows[task]["stop_reason"] == "provider_error" or rows[task]["request_errors"] >= 3}
        if reasons:
            excluded[task] = reasons
    summary = {
        "runs": RUNS,
        "source_commit": baseline["aster_commit"],
        "all_tasks": {mode: totals(list(rows.values())) for mode, rows in groups.items()},
        "common_tasks": {mode: totals([row for task, row in rows.items() if task not in excluded])
                         for mode, rows in groups.items()},
        "excluded_from_common": excluded,
        "paired_changes": {},
        "per_task": {task: {mode: {key: rows[task][key] for key in (
            "passed", "normal_delivery_and_passed", "phase", "stop_reason", "requests", "missing_usage",
            "request_errors", "input_tokens", "cached_tokens", "output_tokens", "weighted_cost", "seconds",
            "timed_out", "final_revisions", "turns", "steps", "task_requirements",
            "state_available",
        )} for mode, rows in groups.items()} for task in baseline["tasks"]},
    }
    for before, after in (("benchmark", "default"), ("benchmark", "off"), ("default", "off")):
        summary["paired_changes"][f"{before}_to_{after}"] = {
            "gained": [task for task in baseline["tasks"]
                       if not groups[before][task]["passed"] and groups[after][task]["passed"]],
            "lost": [task for task in baseline["tasks"]
                     if groups[before][task]["passed"] and not groups[after][task]["passed"]],
        }
    (HERE / "comparison.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    text = "全部 30 题：\n\n" + table(summary["all_tasks"])
    text += "\n\n三组共同可比题（任一组供应商故障或请求错误 ≥3 时共同剔除）：\n\n"
    text += table(summary["common_tasks"])
    text += "\n\n费用为统一权重下的已知用量估算，缺失用量未补齐，不是完整账单。任务耗时是各题之和，不是并行运行的墙钟时间。\n"
    (HERE / "TABLES.md").write_text(text)
    print(text)


if __name__ == "__main__":
    main()
