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


def context_budgets(report):
    configured = report.get("effective_context_budgets", report["protocol"]["context_budgets"])
    selected = {row["variant"] for row in report["manifest"] if row["variant"] in configured}
    budgets = {}
    for variant in selected:
        window = configured[variant]
        if variant in report["protocol"]["context_budgets"]:
            if window != report["protocol"]["context_budgets"][variant]:
                raise ValueError("named budget changed from the frozen protocol")
        elif (variant != f"input_{window - report['settings']['output_tokens']}"
              or report.get("input_budget_override") != window - report["settings"]["output_tokens"]):
            raise ValueError("custom budget must use its explicit input token label")
        budgets[variant] = window - report["settings"]["output_tokens"]
    if len(set(budgets.values())) != len(budgets):
        raise ValueError("duplicate input budgets are ambiguous")
    return budgets


def index(report):
    rows = [row for row in report["rows"] if row["experiment"] == "context"]
    indexed = {(row["task_id"], row["variant"], row["attempt"]): row for row in rows}
    budgets = context_budgets(report)
    manifest = [row for row in report["manifest"] if row["variant"] in budgets]
    expected = {
        (task, variant, attempt)
        for task in {row["task_id"] for row in manifest}
        for variant in budgets
        for attempt in range(1, report["repeats"] + 1)
    }
    planned = {
        (row["task_id"], row["variant"], row["attempt"])
        for row in manifest
    }
    if (not rows or len(indexed) != len(rows) or len(planned) != len(manifest)
            or indexed.keys() != expected or planned != expected):
        raise ValueError("incomplete or duplicate context attempts")
    if any(row.get("setup", {}).get("selected", {}).get("budget", budgets[row["variant"]])
           != budgets[row["variant"]] for row in rows):
        raise ValueError("observed request budget differs from the declared condition")
    return indexed


def matching_settings(before, after):
    a, b = before["settings"].copy(), after["settings"].copy()
    changes = []
    if "tool_output_tokens" in a and "tool_output_tokens" not in b:
        previous = a.pop("tool_output_tokens")
        if after.get("ignored_legacy_settings") != {"tool_output_tokens": previous}:
            raise ValueError("removed tool_output_tokens setting must be explicitly recorded")
        changes.append({"setting": "tool_output_tokens", "before": previous, "after": "removed"})
    if a != b:
        raise ValueError("before/after settings differ outside the declared setting removal")
    return changes


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
    if {key[0] for key in a} != {key[0] for key in b} or before["repeats"] != after["repeats"]:
        raise ValueError("before/after must contain the same complete task set and repeat count")
    if before["protocol"] != after["protocol"]:
        raise ValueError("before/after experimental protocol differs")
    setting_changes = matching_settings(before, after)
    left_budgets, right_budgets = context_budgets(before), context_budgets(after)
    shared = sorted(set(left_budgets.values()) & set(right_budgets.values()), reverse=True)
    if not shared:
        raise ValueError("no shared input budget; do not present different budgets as a same-budget comparison")
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
    groups = {}
    compared_conditions = []
    for budget in shared:
        left_variant = next(variant for variant, value in left_budgets.items() if value == budget)
        right_variant = next(variant for variant, value in right_budgets.items() if value == budget)
        compared_conditions.append({
            "input_budget": budget, "before_variant": left_variant, "after_variant": right_variant,
        })
        for task, _, attempt in [key for key in a if key[1] == left_variant]:
            if a[(task, left_variant, attempt)]["category"] != b[(task, right_variant, attempt)]["category"]:
                raise ValueError("task categories changed")
    for category in ["primary", *sorted({row["category"] for row in a.values()})]:
        for condition in compared_conditions:
            left_variant, right_variant = condition["before_variant"], condition["after_variant"]
            keys = [
                key for key, row in a.items()
                if row["variant"] == left_variant and (
                    row["category"] != "loss_control" if category == "primary"
                    else row["category"] == category
                )
            ]
            left = [a[key] for key in keys]
            right = [b[(task, right_variant, attempt)] for task, _, attempt in keys]
            group = paired(left, right)
            group["before"].update(scores(left))
            group["after"].update(scores(right))
            groups[f"{category}/{right_variant}"] = group
    return {
        "schema_version": 1,
        "scope": "Historical before/after comparison; same task/budget/attempt, not a concurrent randomized trial.",
        "sources": {"before": before["source"], "after": after["source"]},
        "dataset_sha256": hashes,
        "settings": after["settings"],
        "protocol": after["protocol"],
        "setting_changes": setting_changes,
        "compared_conditions": compared_conditions,
        "uncompared_conditions": {
            "before": {key: value for key, value in left_budgets.items() if value not in shared},
            "after": {key: value for key, value in right_budgets.items() if value not in shared},
        },
        "repeats": after["repeats"],
        "groups": groups,
        "failures": {
            version: [
                {key: row.get(key) for key in (
                    "task_id", "variant", "attempt", "stop_reason", "failure", "trace_path"
                )}
                for row in indexed.values() if not row["passed"]
                and row["variant"] in {condition[f"{version}_variant"] for condition in compared_conditions}
            ]
            for version, indexed in (("before", a), ("after", b))
        },
    }


def markdown(report):
    def pct(value):
        return "—" if value is None else f"{value:.2%}"

    lines = [
        "# 上下文重构前后：同题、同预算对照", "",
        "题目、长历史、初始仓库和隐藏判分器哈希一致；仅比较双方共同的实际输入预算，其他条件明确列出。",
        "除下列已声明的实现设置变更外，模型与运行参数一致。",
        *[f"设置变更：{item['setting']} 从 {item['before']} 变为 {item['after']}。" for item in report["setting_changes"]],
        f"未参与对比的预算条件：{json.dumps(report['uncompared_conditions'], ensure_ascii=False)}。",
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
        f"每题每档重复 {report['repeats']} 次，Pass@k 先逐题估计再取平均；36 次尝试不是 Pass@3 的分母。", "",
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
