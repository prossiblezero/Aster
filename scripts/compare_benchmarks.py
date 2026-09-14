"""Compare fixed Aster attempts without mixing task families or experimental arms."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean

SUITES = {
    "repository": "仓库修改",
    "function": "函数实现",
    "memory": "跨运行记忆",
    "harness": "Harness 行为",
}
VARIANTS = {
    "core": "核心工具",
    "all": "全部工具",
    "memory_off": "关闭长期记忆",
    "memory_on": "开启长期记忆",
}


def index_rows(report: dict) -> dict:
    rows = {}
    for row in report["rows"]:
        key = row["suite"], row["variant"], row["task_id"], row["attempt"]
        if key in rows:
            raise ValueError(f"duplicate attempt: {key}")
        rows[key] = row
    tasks = {(r["suite"], r["task_id"]) for r in rows.values()}
    if len(tasks) != report["selected_tasks"]:
        raise ValueError("incomplete report: selected_tasks does not match results")
    expected = {
        (suite, variant, task, attempt)
        for suite, task in tasks
        for variant in (
            ("memory_off", "memory_on")
            if suite == "memory"
            else ("core", "all")
            if report["mode"] == "real"
            else ("core",)
        )
        for attempt in range(1, report["repeats"] + 1)
    }
    if set(rows) != expected:
        raise ValueError("incomplete report: missing or unexpected task/variant/attempt")
    return rows


def metrics(rows: list[dict]) -> dict:
    return {
        "attempts": len(rows),
        "passed": sum(r["passed"] for r in rows),
        "mean_tool_calls": mean(r["steps"] for r in rows) if rows else None,
        "mean_tokens": mean(r["total_tokens"] for r in rows) if rows else None,
        "mean_seconds": mean(r["seconds"] for r in rows) if rows else None,
        "total_tool_calls": sum(r["steps"] for r in rows),
        "total_tokens": sum(r["total_tokens"] for r in rows),
        "read_calls": sum(r.get("read_calls", 0) for r in rows),
        "failed_without_reported_tokens": sum(
            not r["passed"] and not r["total_tokens"] for r in rows
        ),
        "failure_counts": dict(Counter(r["stop_reason"] for r in rows if not r["passed"])),
    }


def paired(left: list[dict], right: list[dict]) -> dict:
    """Pair by fixed task and attempt; callers keep mode, suite, and arm separate."""
    a = {(r["task_id"], r["attempt"]): r for r in left}
    b = {(r["task_id"], r["attempt"]): r for r in right}
    if len(a) != len(left) or len(b) != len(right) or a.keys() != b.keys():
        raise ValueError("unpaired task attempts")
    common = sorted(k for k in a if a[k]["passed"] and b[k]["passed"])
    before, after = metrics([a[k] for k in common]), metrics([b[k] for k in common])
    return {
        "before": metrics(left),
        "after": metrics(right),
        "attempt_pairs": [
            {
                "task_id": key[0],
                "attempt": key[1],
                "before": {
                    name: a[key].get(name)
                    for name in ("passed", "steps", "total_tokens", "seconds", "stop_reason")
                },
                "after": {
                    name: b[key].get(name)
                    for name in ("passed", "steps", "total_tokens", "seconds", "stop_reason")
                },
            }
            for key in sorted(a)
        ],
        "fixed": [list(k) for k in sorted(a) if not a[k]["passed"] and b[k]["passed"]],
        "regressed": [list(k) for k in sorted(a) if a[k]["passed"] and not b[k]["passed"]],
        "common_success": {
            "pairs": [list(k) for k in common],
            "before": before,
            "after": after,
            "token_reduction": (1 - after["total_tokens"] / before["total_tokens"])
            if before["total_tokens"]
            else None,
        },
    }


def compare_mode(before: dict, after: dict) -> dict:
    a, b = index_rows(before), index_rows(after)
    if before["mode"] != after["mode"] or a.keys() != b.keys():
        raise ValueError("before and after must cover exactly the same mode and attempt keys")
    groups = defaultdict(list)
    for key in a:
        groups[key[:2]].append(key)
    return {
        f"{suite}/{variant}": paired([a[k] for k in keys], [b[k] for k in keys])
        for (suite, variant), keys in sorted(groups.items())
    }


def compare_variants(report: dict) -> dict:
    indexed = index_rows(report)
    result = {}
    for suite in ("repository", "function", "memory"):
        left, right = ("memory_off", "memory_on") if suite == "memory" else ("all", "core")
        a = [r for (s, v, _, _), r in indexed.items() if (s, v) == (suite, left)]
        b = [r for (s, v, _, _), r in indexed.items() if (s, v) == (suite, right)]
        if a or b:
            result[f"{suite}/{left}_to_{right}"] = dict(paired(a, b), left=left, right=right)
    return result


def compare_context(before: list[dict], after: list[dict]) -> dict:
    a, b = {r["id"]: r for r in before}, {r["id"]: r for r in after}
    if a.keys() != b.keys() or len(a) != len(before) or len(b) != len(after):
        raise ValueError("unpaired context configurations")

    def summarize(rows):
        return {
            "configurations": len(rows),
            "within_budget": sum(r["within_budget"] for r in rows),
            "request_preserved": sum(r["request_preserved"] for r in rows),
            "evidence_preserved": sum(r["evidence_preserved"] for r in rows),
            "mean_input_after": mean(r["input_after"] for r in rows) if rows else None,
        }

    return {
        "before": summarize(before),
        "after": summarize(after),
        "rows": [{"id": key, "before": a[key], "after": b[key]} for key in sorted(a)],
        "measurement_note": "v3 起估算最终序列化请求（含工具定义与协议字段）；更早的 v2 仅估算 input/tools。比较采用各报告的计量边界，均为本地 tokenizer 估算而非计费 token。",
    }


def build_report(before_scripted, before_real, after_scripted, after_real, mechanisms=None):
    return {
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "scope": "Fixed task/variant/attempt comparisons; no resampling or merged-condition Pass@k.",
        "scripted": compare_mode(before_scripted, after_scripted),
        "real": compare_mode(before_real, after_real),
        "real_variant_comparisons": {
            "before": compare_variants(before_real),
            "after": compare_variants(after_real),
        },
        "context": compare_context(before_scripted["context"], after_scripted["context"]),
        "mechanisms": (
            {
                k: mechanisms[k]
                for k in ("unique_cases", "attempts", "contract_passed", "summary", "limitations")
            }
            if mechanisms
            else None
        ),
        "failures": {
            version: [
                {
                    k: row.get(k)
                    for k in (
                        "task_id",
                        "suite",
                        "variant",
                        "attempt",
                        "failure",
                        "stop_reason",
                        "trace_path",
                    )
                }
                for row in report["rows"]
                if not row["passed"]
            ]
            for version, report in (
                ("before_real", before_real),
                ("after_real", after_real),
                ("before_scripted", before_scripted),
                ("after_scripted", after_scripted),
            )
        },
    }


def number(value):
    return "—" if value is None else f"{value:,.2f}"


def token_change(reduction):
    if reduction is None:
        return "—"
    return f"{'减少' if reduction >= 0 else '增加'} {abs(reduction):.1%}"


def markdown(report):
    lines = [
        "# Aster 修复前后对照",
        "",
        "按任务类型和实验条件分别统计。每行对齐同一任务编号、同一条件、同一次尝试；失败保留，不合并不同开关为重试。",
        "",
    ]
    for mode, title in (("real", "真实模型：全部尝试"), ("scripted", "确定性回放：全部尝试")):
        lines += [
            f"## {title}",
            "",
            "| 任务 / 条件 | 修复前通过 | 修复后通过 | 平均工具调用 前→后 | 平均记录 token 前→后 | 平均耗时秒 前→后 |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        ]
        for key, group in report[mode].items():
            suite, variant = key.split("/")
            a, b = group["before"], group["after"]
            lines.append(
                f"| {SUITES[suite]} / {VARIANTS[variant]} | {a['passed']}/{a['attempts']} | {b['passed']}/{b['attempts']} | {number(a['mean_tool_calls'])}→{number(b['mean_tool_calls'])} | {number(a['mean_tokens'])}→{number(b['mean_tokens'])} | {number(a['mean_seconds'])}→{number(b['mean_seconds'])} |"
            )
    lines += [
        "",
        "失败可能提前终止，因此全部尝试的平均成本要结合完成率阅读；零 token 表示未收到可计量 usage，不保证远端没有消耗。确定性回放不调用模型，token 为零。",
        "",
        "## 补充：修复前后均成功的同题成本",
        "",
        "只用于排除提前失败对成本的影响，不能替代上面的全样本成功率。未同时成功的题目仍在主表与失败清单中。",
        "",
        "| 任务 / 条件 | 同时成功尝试数 | 平均工具调用 前→后 | 平均 token 前→后 | token 变化 |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for key, group in report["real"].items():
        suite, variant = key.split("/")
        common = group["common_success"]
        a, b = common["before"], common["after"]
        rate = token_change(common["token_reduction"])
        lines.append(
            f"| {SUITES[suite]} / {VARIANTS[variant]} | {a['attempts']} | {number(a['mean_tool_calls'])}→{number(b['mean_tool_calls'])} | {number(a['mean_tokens'])}→{number(b['mean_tokens'])} | {rate} |"
        )
    lines += [
        "",
        "## 仓库任务逐题结果",
        "",
        "单列多文件任务，避免用函数题的平均成本掩盖仓库任务的差异。",
        "",
        "| 任务 / 条件 | 完成 前→后 | 工具调用 前→后 | token 前→后 |",
        "| --- | --- | ---: | ---: |",
    ]
    for key, group in report["real"].items():
        suite, variant = key.split("/")
        if suite != "repository":
            continue
        for pair in group["attempt_pairs"]:
            a, b = pair["before"], pair["after"]
            completion = f"{'通过' if a['passed'] else '失败'}→{'通过' if b['passed'] else '失败'}"
            lines.append(
                f"| {pair['task_id']} / {VARIANTS[variant]} / 第 {pair['attempt']} 次 | {completion} | {a['steps']}→{b['steps']} | {a['total_tokens']:,}→{b['total_tokens']:,} |"
            )
    lines += [
        "",
        "## 修复后：同题工具集与长期记忆开关对照",
        "",
        "全部尝试的完成率先列出，成本补充仅比较两个条件都成功的同题；开关差异不能算模型重试。",
        "",
        "| 任务 / 条件变化 | 全部尝试通过 前→后 | 两组均成功题次 | 同题工具总次数 前→后 | 同题 token 总数 前→后 | token 变化 |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for key, group in report["real_variant_comparisons"]["after"].items():
        suite = key.split("/")[0]
        a, b, common = group["before"], group["after"], group["common_success"]
        ca, cb = common["before"], common["after"]
        rate = token_change(common["token_reduction"])
        lines.append(
            f"| {SUITES[suite]} / {VARIANTS[group['left']]}→{VARIANTS[group['right']]} | {a['passed']}/{a['attempts']}→{b['passed']}/{b['attempts']} | {ca['attempts']} | {ca['total_tool_calls']}→{cb['total_tool_calls']} | {ca['total_tokens']:,}→{cb['total_tokens']:,} | {rate} |"
        )
    context = report["context"]
    a, b = context["before"], context["after"]
    lines += [
        "",
        "## 上下文与机制",
        "",
        f"上下文预算通过：{a['within_budget']}/{a['configurations']}→{b['within_budget']}/{b['configurations']}；修复后当前请求保留 {b['request_preserved']}/{b['configurations']}，指定证据保留 {b['evidence_preserved']}/{b['configurations']}。",
        context["measurement_note"],
    ]
    mechanisms = report["mechanisms"]
    if mechanisms:
        lines += [
            "",
            f"另有 {mechanisms['unique_cases']} 个固定机制场景、{mechanisms['attempts']} 次尝试，契约通过 {mechanisms['contract_passed']}/{mechanisms['attempts']}。这些场景使用固定动作和标注反馈，不是自主模型任务分数。",
            "",
            "| 机制 | 关闭时目标达成 | 开启时目标达成 |",
            "| --- | ---: | ---: |",
        ]
        for title, before, after in (
            ("记忆反馈调整", "memory_feedback/evolution_off", "memory_feedback/evolution_on"),
            ("循环恢复", "recovery/recovery_off", "recovery/recovery_on"),
            ("前置 Hooks", "hooks/before_hook_off", "hooks/before_hook_on"),
        ):
            a, b = mechanisms["summary"][before], mechanisms["summary"][after]
            lines.append(
                f"| {title} | {a['goal_passed']}/{a['attempts']} | {b['goal_passed']}/{b['attempts']} |"
            )
    lines += ["", "## 修复后失败清单", ""]
    failures = report["failures"]["after_real"] + report["failures"]["after_scripted"]
    for row in failures:
        reason = (row["failure"] or row["stop_reason"] or "无原因记录").replace("\n", " ")
        lines.append(
            f"- {row['task_id']} / {VARIANTS[row['variant']]} / 第 {row['attempt']} 次：{reason}"
        )
    if not failures:
        lines.append("当前固定样本未出现失败；这不等于在其他仓库上保证成功。")
    lines += [
        "",
        "完整逐题配对、回归题目、修复前失败和原始报告哈希见 report.json。不同难度不汇总为一个效率分数；本实验也不是完整 HumanEval 或 SWE-bench 官方成绩。",
        "",
    ]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("before-scripted", "before-real", "after-scripted", "after-real"):
        version = "v3" if name.startswith("before") else "v4"
        mode = name.rsplit("-", 1)[1]
        parser.add_argument(
            f"--{name}",
            type=Path,
            default=Path(f"artifacts/benchmark-{version}-{mode}/report.json"),
        )
    parser.add_argument(
        "--mechanisms", type=Path, default=Path("artifacts/benchmark-v4-mechanisms/report.json")
    )
    parser.add_argument("--output", type=Path, default=Path("artifacts/benchmark-v4-comparison"))
    args = parser.parse_args()
    paths = {
        name: getattr(args, name)
        for name in ("before_scripted", "before_real", "after_scripted", "after_real")
    }
    if args.mechanisms.exists():
        paths["mechanisms"] = args.mechanisms
    contents = {name: path.read_bytes() for name, path in paths.items()}
    report = build_report(**{name: json.loads(content) for name, content in contents.items()})
    report["source_reports"] = {
        name: {"path": str(paths[name]), "sha256": hashlib.sha256(content).hexdigest()}
        for name, content in contents.items()
    }
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    )
    (args.output / "report.md").write_text(markdown(report))
    print(f"Wrote {args.output / 'report.md'}")


if __name__ == "__main__":
    main()
