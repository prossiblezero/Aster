"""Aster evaluation entry point. Task content and grading fixtures live in datasets/."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import platform
import subprocess
import tempfile
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean

from benchmark_support import (
    dataset_path,
    external_check,
    load_bank,
    mean_tokens,
    prepare,
    run_memory,
    run_task,
    token_number,
    usage_label,
    usage_metrics,
    usage_summary,
)

from aster.config import load_settings
from aster.context import ContextBuilder
from aster.provider import request_payload
from aster.schema import RunState, TaskSpec
from aster.tools import CORE_TOOLS, definitions


def source_identity():
    repo = Path(__file__).resolve().parents[1]
    digest = hashlib.sha256()
    for folder in ("datasets", "src", "scripts"):
        for path in sorted((repo / folder).rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
                digest.update(str(path.relative_to(repo)).encode())
                digest.update(path.read_bytes())
    return {
        "git_head": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=repo, text=True
        ).strip(),
        "content_sha256": digest.hexdigest(),
        "python": platform.python_version(),
    }


def pass_at_k(rows, k):
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["task_id"]].append(row["passed"])
    if not grouped or any(len(v) < k for v in grouped.values()):
        return None
    # HumanEval estimator; count attempts, not only the first sample.
    return mean(1 - math.comb(len(v) - sum(v), k) / math.comb(len(v), k) for v in grouped.values())


def summarize(rows):
    result = {}
    groups = defaultdict(list)
    for row in rows:
        groups[(row["suite"], row["variant"])].append(row)
    for (suite, variant), values in sorted(groups.items()):
        success = [v for v in values if v["passed"]]
        result[f"{suite}/{variant}"] = {
            "tasks": len({v["task_id"] for v in values}),
            "attempts": len(values),
            "passed": len(success),
            "pass_rate": len(success) / len(values),
            "pass_at_1": pass_at_k(values, 1),
            "pass_at_3": pass_at_k(values, 3),
            "mean_steps_all": mean(v["steps"] for v in values),
            "mean_tokens_all": mean_tokens(values),
            "usage_summary": usage_summary(values),
            "mean_seconds_all": mean(v["seconds"] for v in values),
            "mean_steps_success": mean(v["steps"] for v in success) if success else None,
            "mean_tokens_success": mean_tokens(success),
            "read_calls": sum(v["read_calls"] for v in values),
        }
    return result


def context_cases():
    data = json.loads(dataset_path("context_cases.json").read_text())
    rows = []
    tool_defs = definitions(sorted(CORE_TOOLS))
    for case in data["cases"]:
        state = RunState(task=TaskSpec(prompt=data["prompt"]))
        template = data["templates"][case["content"]]
        state.messages = [
            {"role": "tool", "content": template.format(index=i) * 12}
            for i in range(case["observations"])
        ]
        state.messages.append({"role": "tool", "content": data["evidence"], "important": True})
        raw = ContextBuilder(1000000, case["reserve"])
        full, _ = raw.build(state.model_copy(deep=True), state.task.prompt, tools=tool_defs)
        builder = ContextBuilder(case["budget"], case["reserve"])
        prompt, report = builder.build(state, state.task.prompt, tools=tool_defs)
        # Measure exactly the JSON fields sent to the provider, including tool schemas.
        before = builder.count(request_payload(full, tool_defs, output_tokens=case["reserve"]))
        after = builder.count(request_payload(prompt, tool_defs, output_tokens=case["reserve"]))
        rendered = json.dumps(prompt, ensure_ascii=False)
        rows.append(
            {
                "id": case["id"],
                "content": case["content"],
                "observations": case["observations"],
                "input_before": before,
                "input_after": after,
                "budget": report.budget,
                "within_budget": after <= report.budget,
                "request_preserved": data["prompt"] in rendered,
                "evidence_preserved": data["evidence"] in rendered,
                "reported_tokens": report.estimated_tokens,
                "removed_messages": report.removed_messages,
            }
        )
    return rows


async def baseline_check(task):
    with tempfile.TemporaryDirectory(prefix="aster-baseline-") as directory:
        root = Path(directory) / "workspace"
        prepare(task, root)
        check = task["checks"]["verifier"]
        fix = await external_check(root, check, "test_fix")
        regression = await external_check(root, check, "test_regression")
        return {
            "task_id": task["id"],
            "fix_fails_before": fix["exit_code"] == 1
            and "failed" in fix["output"]
            and "ERROR collecting" not in fix["output"],
            "regression_passes_before": regression["passed"],
            "fix": fix,
            "regression": regression,
        }


def markdown(report):
    lines = [
        "# Aster benchmark",
        "",
        f"模式：{report['mode']}；任务内容哈希：`{report['source']['content_sha256']}`。",
        "",
        "脚本动作回放验证 Harness；真实模型独立完成任务才计入模型成功率。每次运行均用全新 fixture，独立验证器只在运行结束后加入隔离副本。",
        "",
        "| 任务组/条件 | 独立任务 | 尝试 | 通过 | 全部尝试平均步数 | 平均已报告 token | 已报告总 token / 请求覆盖 |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for key, s in report["summary"].items():
        lines.append(
            f"| {key} | {s['tasks']} | {s['attempts']} | {s['passed']}/{s['attempts']} | {s['mean_steps_all']:.2f} | {token_number(s['mean_tokens_all'])} | {usage_label(s['usage_summary'])} |"
        )
    lines += [
        "",
        "记忆对照的两组都有相同的读取和存储阶段（各 2 次工具调用），表中仅统计新 Runtime 的后续问答；关闭记忆组仍保留同一源文件。",
        "用量缺失为 —；明确报告的 0 保留为 0。覆盖为已报告该指标的请求数/模型调用数，逐指标详情见 JSON。部分用量及覆盖未知的历史记录不能当作完整费用；服务端隐藏重试的成本未知。",
    ]
    if report.get("baseline_checks"):
        checks = report["baseline_checks"]
        good = sum(x["fix_fails_before"] and x["regression_passes_before"] for x in checks)
        lines += [
            "",
            f"验证器反向检查：{good}/{len(checks)} 原始版本未通过目标测试，且原有行为测试通过。参考修复的结果见 scripted 行。",
        ]
    if report.get("context"):
        rows = report["context"]
        n = len(rows)
        lines += [
            "",
            f"上下文配置 {n} 组：含工具定义的输入平均 {mean(x['input_before'] for x in rows):.0f} → {mean(x['input_after'] for x in rows):.0f} tokens；预算内 {sum(x['within_budget'] for x in rows)}/{n}；当前请求保留 {sum(x['request_preserved'] for x in rows)}/{n}。",
            "这些 token 是本地 tokenizer 对请求 JSON 的估计值，实际计费以远程 usage 为准。",
        ]
    if report["mode"] == "real":
        lines += [
            "",
            "## 逐题结果",
            "",
            "core/all 分别为核心工具集/全部工具；memory_off/on 为关闭/开启记忆。两个条件不合并为一次重试。",
            "",
            "| 任务 | 条件 | 完成 | 外部代码测试 | 工具调用 | 已报告总 token / 请求覆盖 | 秒 | 结束原因 |",
            "|---|---|---|---|---:|---:|---:|---|",
        ]
        for row in report["rows"]:
            tests = row.get("verifier")
            verified = ("通过" if tests["passed"] else "失败") if tests else "不适用"
            lines.append(
                f"| {row['task_id']} | {row['variant']} | {'通过' if row['passed'] else '失败'} | {verified} | {row['steps']} | {usage_label(usage_summary([row]))} | {row['seconds']:.2f} | {row['stop_reason']} |"
            )
    if report.get("grading_corrections"):
        lines += [
            "",
            "评分修正：端点题未指定 JSON 内容，删除验证器额外添加的 status=ok 条件后，对两个条件的原始产物重新评分；未重新调用模型。原判定与修正证据见 grader-corrections.json，原始尝试记录保持不变。",
        ]
    tools = {}
    for row in report["rows"]:
        for name, counts in row.get("tools", {}).items():
            total = tools.setdefault(name, {"calls": 0, "success": 0, "reasons": {}})
            total["calls"] += counts["calls"]
            total["success"] += counts["success"]
            for reason, count in counts["reasons"].items():
                total["reasons"][reason] = total["reasons"].get(reason, 0) + count
    if tools:
        lines += [
            "",
            "## 工具结果",
            "",
            "按全部尝试统计，安全拒绝和测试断言失败保留各自原因；任务完成不掩盖工具失败。",
            "",
            "| 工具 | 调用 | 成功 | 其他结果原因及次数 |",
            "|---|---:|---:|---|",
        ]
        for name, counts in sorted(tools.items()):
            reasons = ", ".join(
                f"{reason}: {count}" for reason, count in sorted(counts["reasons"].items())
            )
            lines.append(f"| {name} | {counts['calls']} | {counts['success']} | {reasons or '—'} |")
    failures = [r for r in report["rows"] if not r["passed"]]
    lines += ["", "## 失败与边界", ""]
    for row in failures:
        lines.append(
            f"- `{row['task_id']}` / {row['variant']} / attempt {row['attempt']}: {row['failure']}（{row['stop_reason']}）"
        )
    if not failures:
        lines.append("- 当前已执行条件未出现任务失败。")
    lines += [
        "",
        "题库包含 HumanEval 全部 164 题，使用文件工具形式；实际执行题目以 selected_tasks 和逐条结果为准，参考答案回放不能充当模型得分。真实模型默认文档命令使用固定的 12 题子集，不能作为完整 HumanEval 分数。仓库题借鉴 SWE-bench 的验证方式，未运行 SWE-bench 官方全集。所有题目仅用于 evaluation，不得同时充当后续 SFT/GRPO 训练样本。",
        "",
    ]
    return "\n".join(lines)


async def evaluate(args):
    if args.real and not (args.suite or args.task or args.selection):
        args.selection = "real_subset.json"
    tasks = load_bank()
    if args.real:
        tasks = [t for t in tasks if t["suite"] != "harness"]
    if args.suite:
        tasks = [t for t in tasks if t["suite"] == args.suite]
    if args.task:
        tasks = [t for t in tasks if t["id"] in args.task]
    if args.selection:
        selected = set(json.loads(dataset_path(args.selection).read_text())["task_ids"])
        available = {t["id"] for t in load_bank()}
        if selected - available:
            raise ValueError(f"unknown tasks in selection: {sorted(selected - available)}")
        tasks = [t for t in tasks if t["id"] in selected]
    if not tasks:
        raise ValueError("empty evaluation selection")
    mode = "real" if args.real else "scripted"
    output = Path(args.output or f"artifacts/benchmark-v4-{mode}")
    output.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    archive = output / "runs" / stamp
    settings = load_settings(Path(args.env_file), sandbox="required", approval="auto")
    if args.real and not settings.api_key.get_secret_value():
        raise ValueError("real mode needs configured API key")
    report = {
        "name": "Aster benchmark",
        "mode": mode,
        "at": datetime.now(UTC).isoformat(),
        "source": source_identity(),
        "settings": settings.identity(),
        "repeats": args.repeats,
        "selected_tasks": len(tasks),
        "selection_file": args.selection,
        "rows": [],
        "baseline_checks": [],
        "context": [],
    }
    if not args.real:
        for task in tasks:
            if task["checks"].get("verifier"):
                result = await baseline_check(task)
                report["baseline_checks"].append(result)
                print(
                    f"grader {task['id']}: baseline_target_fails={result['fix_fails_before']}, regression_passes={result['regression_passes_before']}",
                    flush=True,
                )
        if not args.suite and not args.task and not args.selection:
            report["context"] = context_cases()
    jobs = []
    for task in tasks:
        variants = (
            ("memory_off", "memory_on")
            if task["suite"] == "memory"
            else (("core", "all") if args.real else ("core",))
        )
        for attempt in range(1, args.repeats + 1):
            for variant in variants:
                jobs.append((task, attempt, variant))
    semaphore = asyncio.Semaphore(args.concurrency)
    with tempfile.TemporaryDirectory(prefix="aster-benchmark-v4-") as directory:

        async def run_one(task, attempt, variant):
            async with semaphore:
                relative = Path(task["id"]) / f"{variant}-{attempt}"
                root = Path(directory) / relative / "workspace"
                destination = archive / relative
                try:
                    if task["suite"] == "memory":
                        row = await run_memory(
                            task,
                            root,
                            destination,
                            settings,
                            real=args.real,
                            enabled=variant == "memory_on",
                        )
                    else:
                        row = await run_task(
                            task, root, destination, settings, real=args.real, variant=variant
                        )
                except Exception as exc:
                    row = {
                        "task_id": task["id"],
                        "suite": task["suite"],
                        "variant": variant,
                        "mode": mode,
                        "passed": False,
                        "failure": f"{type(exc).__name__}: {exc}",
                        "stop_reason": "evaluation_setup_error",
                        "steps": 0,
                        "turns": 0,
                        "read_calls": 0,
                        **usage_metrics({}),
                        "seconds": 0,
                    }
                row["attempt"] = attempt
                row["batch_id"] = stamp
                report["rows"].append(row)
                with (output / "attempts.jsonl").open("a") as stream:
                    stream.write(json.dumps(row, ensure_ascii=False) + "\n")
                print(
                    f"{len(report['rows'])}/{len(jobs)} {task['id']} {variant} #{attempt}: {'PASS' if row['passed'] else 'FAIL'} {row['failure'][:160]}",
                    flush=True,
                )

        await asyncio.gather(*(run_one(*job) for job in jobs))
    report["rows"].sort(key=lambda r: (r["suite"], r["task_id"], r["variant"], r["attempt"]))
    report["summary"] = summarize(report["rows"])
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    (output / "report.md").write_text(markdown(report))
    archive.mkdir(parents=True, exist_ok=True)
    (archive / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(f"Report: {output / 'report.md'}", flush=True)
    failures = sum(not row["passed"] for row in report["rows"])
    baseline_failures = sum(
        not (x["fix_fails_before"] and x["regression_passes_before"])
        for x in report["baseline_checks"]
    )
    context_failures = sum(
        not (x["within_budget"] and x["request_preserved"] and x["evidence_preserved"])
        for x in report["context"]
    )
    return int(bool(failures or baseline_failures or context_failures))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--real", action="store_true")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--output")
    parser.add_argument("--suite", choices=["harness", "repository", "function", "memory"])
    parser.add_argument("--task", action="append")
    parser.add_argument("--selection", help="Dataset JSON containing a fixed task_ids list")
    args = parser.parse_args()
    if args.repeats < 1 or not 1 <= args.concurrency <= 4:
        parser.error("repeats >= 1; concurrency 1..4")
    return asyncio.run(evaluate(args))


if __name__ == "__main__":
    raise SystemExit(main())
