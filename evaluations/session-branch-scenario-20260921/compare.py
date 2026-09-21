"""Build the frozen summary for the focused branch scenario without API calls."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PATHS = {
    "baseline": ("legacy_summary", "legacy_checkpoint"),
    "candidate": ("branch_clean", "branch_excerpt"),
}


def load(name, mode):
    return json.loads((ROOT / f"{name}-{mode}.json").read_text())


def rows_for(name, mode):
    rows = load(name, mode)["rows"]
    assert {row["path"] for row in rows} == set(PATHS[name])
    assert all(row["passed"] for row in rows)
    return {row["path"]: row for row in rows}


def summarize(row):
    usage = row["usage"]
    return {
        "path": row["path"],
        "passed": row["passed"],
        "operation_count": row["operation_count"],
        "manual_handoff_characters": row["manual_handoff_characters"],
        "preparation_model_calls": row["preparation_model_calls"],
        "model_requests": usage["model_requests"],
        "input_tokens": usage["input_tokens"],
        "output_tokens": usage["output_tokens"],
        "total_tokens": usage["total_tokens"],
        "http": row["http"],
        "constraints_preserved": row["constraints_preserved"],
        "future_markers_in_final_prompt": row["future_markers_in_final_prompt"],
    }


def main():
    environment = json.loads((ROOT / "environment.json").read_text())
    scripted = {name: rows_for(name, "scripted") for name in PATHS}
    real = {name: rows_for(name, "real") for name in PATHS}
    assert environment["baseline"]["settings"] == environment["candidate"]["settings"]
    assert all(row["source"] == environment[name] for name, group in real.items() for row in group.values())
    assert all(row["source"] == environment[name] for name, group in scripted.items() for row in group.values())
    assert all(row["http"]["http_attempts"] == row["http"]["http_attempts_with_usage"] for group in real.values() for row in group.values())
    compaction = {
        name: json.loads((ROOT / f"{name}-compaction.json").read_text()) for name in PATHS
    }
    assert all(item["passed"] for item in compaction.values())
    output = {
        "scope": "i=4, j=3; fixed course-export constraints; one real attempt per path",
        "environment": environment,
        "scripted": {name: [summarize(row) for row in group.values()] for name, group in scripted.items()},
        "real": {name: [summarize(row) for row in group.values()] for name, group in real.items()},
        "compaction_probe": compaction,
        "paired_findings": {
            "legacy_summary_to_branch_excerpt": {
                "operation_count": [real["baseline"]["legacy_summary"]["operation_count"], real["candidate"]["branch_excerpt"]["operation_count"]],
                "manual_handoff_characters": [real["baseline"]["legacy_summary"]["manual_handoff_characters"], real["candidate"]["branch_excerpt"]["manual_handoff_characters"]],
                "total_tokens": [real["baseline"]["legacy_summary"]["usage"]["total_tokens"], real["candidate"]["branch_excerpt"]["usage"]["total_tokens"]],
                "model_requests": [real["baseline"]["legacy_summary"]["usage"]["model_requests"], real["candidate"]["branch_excerpt"]["usage"]["model_requests"]],
            },
            "legacy_checkpoint_to_branch_clean": {
                "operation_count": [real["baseline"]["legacy_checkpoint"]["operation_count"], real["candidate"]["branch_clean"]["operation_count"]],
                "manual_handoff_characters": [real["baseline"]["legacy_checkpoint"]["manual_handoff_characters"], real["candidate"]["branch_clean"]["manual_handoff_characters"]],
                "total_tokens": [real["baseline"]["legacy_checkpoint"]["usage"]["total_tokens"], real["candidate"]["branch_clean"]["usage"]["total_tokens"]],
                "model_requests": [real["baseline"]["legacy_checkpoint"]["usage"]["model_requests"], real["candidate"]["branch_clean"]["usage"]["model_requests"]],
            },
        },
        "limitations": [
            "历史由离线替身预置；共同准备成本不计入路径比较。",
            "只有四条路径、一个固定任务和一次真实模型尝试，不能证明普遍非劣。",
            "没有网关价格，货币费用未测；本报告只比较已报告 token。",
            "真实批次验证了最终回答和历史可见性，没有采集真人点击时间。",
        ],
    }
    (ROOT / "comparison.json").write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
