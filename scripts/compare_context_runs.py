"""Compare the same context tasks before and after a Harness change, without model calls."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from collections import Counter
from pathlib import Path

from benchmark import pass_at_k
from compare_benchmarks import number, paired, token_change


def index(report):
    rows = [row for row in report["rows"] if row["experiment"] == "context"]
    indexed = {(row["task_id"], row["variant"], row["attempt"]): row for row in rows}
    expected = {
        (task, variant, attempt)
        for task in {row["task_id"] for row in rows}
        for variant in report["protocol"]["context_budgets"]
        for attempt in range(1, report["repeats"] + 1)
    }
    planned = {
        (row["task_id"], row["variant"], row["attempt"])
        for row in report["manifest"]
        if row["variant"] in report["protocol"]["context_budgets"]
    }
    if not rows or len(indexed) != len(rows) or indexed.keys() != expected or planned != expected:
        raise ValueError("incomplete or duplicate context attempts")
    return indexed


def scores(rows):
    return {
        "tasks": len({row["task_id"] for row in rows}),
        "pass_at_1": pass_at_k(rows, 1),
        "pass_at_3": pass_at_k(rows, 3),
        "hidden_tests_passed": sum(row.get("verifier", {}).get("passed", False) for row in rows),
        "stop_reasons": dict(Counter(row["stop_reason"] for row in rows)),
        "observation_read_calls": sum(row.get("observation_read_calls", 0) for row in rows),
        "observation_read_successes": sum(row.get("observation_read_successes", 0) for row in rows),
        "all_requests_within_budget": all(row.get("all_within_budget") is True for row in rows),
    }


def compare(before, after):
    a, b = index(before), index(after)
    if a.keys() != b.keys():
        raise ValueError("before/after task, budget and attempt keys differ")
    if before["settings"] != after["settings"] or before["protocol"] != after["protocol"]:
        raise ValueError("before/after settings or experimental protocol differ")
    hashes = after["dataset_sha256"]
    baseline_hashes = before.get("dataset_sha256")
    if baseline_hashes is None:
        # Historical reports predate separate dataset hashes; recover from their frozen commit.
        repo = Path(__file__).resolve().parents[1]
        baseline_hashes = {
            name: hashlib.sha256(
                subprocess.check_output(
                    ["git", "show", f"{before['source']['git_head']}:datasets/{name}"], cwd=repo
                )
            ).hexdigest()
            for name in hashes
        }
    if baseline_hashes != hashes:
        raise ValueError("task text, history, fixture or grader changed")
    if any(a[key]["category"] != b[key]["category"] for key in a):
        raise ValueError("task categories changed")
    groups = {}
    for category in ["primary", *sorted({row["category"] for row in a.values()})]:
        for variant in after["protocol"]["context_budgets"]:
            keys = [
                key for key, row in a.items()
                if row["variant"] == variant and (
                    row["category"] != "loss_control" if category == "primary"
                    else row["category"] == category
                )
            ]
            left, right = [a[key] for key in keys], [b[key] for key in keys]
            group = paired(left, right)
            group["before"].update(scores(left))
            group["after"].update(scores(right))
            groups[f"{category}/{variant}"] = group
    return {
        "schema_version": 1,
        "scope": "Historical before/after comparison; same task/budget/attempt, not a concurrent randomized trial.",
        "sources": {"before": before["source"], "after": after["source"]},
        "dataset_sha256": hashes,
        "settings": after["settings"],
        "protocol": after["protocol"],
        "groups": groups,
        "failures": {
            version: [
                {key: row.get(key) for key in (
                    "task_id", "variant", "attempt", "stop_reason", "failure", "trace_path"
                )}
                for row in indexed.values() if not row["passed"]
            ]
            for version, indexed in (("before", a), ("after", b))
        },
    }


def markdown(report):
    def pct(value):
        return "—" if value is None else f"{value:.2%}"

    lines = [
        "# 上下文重构前后：同题、同预算对照", "",
        "题目、长历史、初始仓库和隐藏判分器哈希一致；模型与运行参数一致。",
        "这是历史批次与新批次的比较，模型和网关的时间波动仍可能影响结果。", "",
        "| 类别 / 预算 | 完成 前→后 | Pass@1 前→后 | Pass@3 前→后 | 隐藏功能通过 前→后 | 预算停止 前→后 | 归档查询/读取调用 前→后 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for name, group in report["groups"].items():
        a, b = group["before"], group["after"]
        lines.append(
            f"| {name} | {a['passed']}/{a['attempts']}→{b['passed']}/{b['attempts']} | "
            f"{pct(a['pass_at_1'])}→{pct(b['pass_at_1'])} | "
            f"{pct(a['pass_at_3'])}→{pct(b['pass_at_3'])} | "
            f"{a['hidden_tests_passed']}→{b['hidden_tests_passed']} | "
            f"{a['stop_reasons'].get('context_budget_exceeded', 0)}→{b['stop_reasons'].get('context_budget_exceeded', 0)} | "
            f"{a['observation_read_calls']}→{b['observation_read_calls']} |"
        )
    lines += [
        "", "primary 是 12 道主任务；loss_control 是原样保留的一次性证据控制，单列不并入主任务。",
        "每题每档重复 3 次，Pass@k 先逐题估计再取平均；36 次尝试不是 Pass@3 的分母。", "",
        "## 两个版本都成功的配对成本", "",
        "失败样本保留在上表和 JSON 清单中；此表只防止把提前失败的低消耗写成成本收益。", "",
        "| 类别 / 预算 | 共同成功 | 平均 token 前→后 | token 变化 | 平均工具调用 前→后 | 平均秒 前→后 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for name, group in report["groups"].items():
        common = group["common_success"]
        a, b = common["before"], common["after"]
        lines.append(
            f"| {name} | {a['attempts']} | {number(a['mean_tokens'])}→{number(b['mean_tokens'])} | "
            f"{token_change(common['token_reduction'])} | "
            f"{number(a['mean_tool_calls'])}→{number(b['mean_tool_calls'])} | "
            f"{number(a['mean_seconds'])}→{number(b['mean_seconds'])} |"
        )
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = compare(json.loads(args.before.read_text()), json.loads(args.after.read_text()))
    report["reports"] = {
        name: {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        for name, path in (("before", args.before), ("after", args.after))
    }
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "comparison.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    (args.output / "comparison.md").write_text(markdown(report))
    print(args.output / "comparison.md")


if __name__ == "__main__":
    main()
