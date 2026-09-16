"""Paired context-budget and recovery-feedback experiments using the production Runtime."""

from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
import random
import tempfile
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean

from benchmark import baseline_check, source_identity
from benchmark_support import (
    ScriptedPolicy,
    archive,
    dataset_path,
    external_check,
    grade_contract,
    load_bank,
    metrics,
    prepare,
    run_task,
    task_spec,
    tool_events,
)

from aster.config import load_settings
from aster.context import ContextBuilder
from aster.evidence import Redactor
from aster.provider import ModelClient, request_payload
from aster.runtime import Runtime
from aster.tools import SCHEMAS


def read_data(name):
    return json.loads(dataset_path(name).read_text())


def dataset_identity(cases, protocol):
    """Hash task text, long history, fixtures and graders independently of implementation."""
    names = {"robustness_protocol.json", protocol["history_file"]}
    names.update(
        "context_task_cases.json" if case["experiment"] == "context" else "recovery_task_cases.json"
        for case in cases
    )
    if any(case["experiment"] == "recovery" for case in cases):
        names.add("benchmark_tasks.json")
    for case in cases:
        names.add(case["checks"]["verifier"])
        fixture = dataset_path(case["fixture"])
        names.update(
            str(path.relative_to(dataset_path(".")))
            for path in fixture.rglob("*")
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
        )
    return {name: hashlib.sha256(dataset_path(name).read_bytes()).hexdigest() for name in sorted(names)}


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def action_key(call, workspace):
    """Compare validated defaults and normalized paths, not provider spelling."""
    try:
        arguments = SCHEMAS[call["name"]][0].model_validate(call["arguments"]).model_dump()
        if "path" in arguments:
            arguments["path"] = str(workspace.path(arguments["path"]).relative_to(workspace.root))
        return call["name"], json.dumps(arguments, sort_keys=True)
    except (KeyError, ValueError):
        return None


def load_cases(suite=None):
    cases = []
    if suite in (None, "context"):
        cases.extend(
            dict(case, experiment="context")
            for case in read_data("context_task_cases.json")["cases"]
        )
    if suite in (None, "recovery"):
        bank = {task["id"]: task for task in load_bank()}
        for case in read_data("recovery_task_cases.json")["cases"]:
            cases.append(
                {
                    **copy.deepcopy(bank[case["base_task"]]),
                    **case,
                    "experiment": "recovery",
                    "max_steps": 40,
                    "max_turns": 28,
                }
            )
    ids = [case["id"] for case in cases]
    if not cases or len(set(ids)) != len(ids):
        raise ValueError("empty selection or duplicate scenario IDs")
    return cases


class SilentRecovery(Runtime):
    """Experimental ablation only: keep detection/budgets, suppress its feedback message."""

    def add_message(self, role, content, *, important=False, native=False):
        if (
            role == "user"
            and content.startswith("Recovery required (")
            and content == self.state.feedback
        ):
            self.evidence.emit(self.state.run_id, "benchmark_feedback_suppressed", feedback=content)
            return
        super().add_message(role, content, important=important, native=native)


class ObservedModel:
    """Record actual requests before forwarding unchanged to the selected model."""

    def __init__(self, runtime, model, destination, evidence):
        self.runtime, self.model, self.destination = runtime, model, destination
        self.evidence = evidence
        self.requests = []

    async def complete(self, messages, tools):
        settings = self.runtime.settings
        payload = request_payload(
            messages,
            tools,
            protocol=settings.protocol,
            model=settings.model,
            output_tokens=settings.output_tokens,
        )
        rendered = json.dumps(payload, ensure_ascii=False)
        row = {
            "turn": self.runtime.state.turn,
            "estimated_tokens": self.runtime.context.count(payload),
            "sha256": hashlib.sha256(rendered.encode()).hexdigest(),
            "evidence_present": [
                json.dumps(text, ensure_ascii=False)[1:-1] in rendered for text in self.evidence
            ],
            "recovery_feedback_present": "Recovery required (" in rendered,
        }
        self.requests.append(row)
        save(
            self.destination / f"request-{len(self.requests):03d}.json", {**row, "payload": payload}
        )
        return await self.model.complete(messages, tools)


def preview(runtime, budget):
    """Measure without mutating the history that the real request will receive."""
    settings = runtime.settings
    _, report = ContextBuilder(budget, settings.output_tokens, settings.tool_output_tokens).build(
        runtime.state.model_copy(deep=True),
        runtime.state.task.prompt,
        structured_history=copy.deepcopy(runtime._structured_history),
        working_memory=runtime.memory.working_section(runtime.state.working_memory),
        tools=runtime.tool_definitions(),
        protocol=settings.protocol,
        model=settings.model,
        archive=lambda call_id, tool, content: runtime.evidence.archive_observation(
            runtime.state.run_id, call_id, tool, content
        ),
    )
    return {
        "estimated_tokens": report.estimated_tokens,
        "budget": report.budget,
        "removed_messages": report.removed_messages,
        "compacted": report.compacted,
        "policy_version": report.policy_version,
    }


async def initialize(case, root, settings, variant, protocol):
    prepare(case, root)
    if case["experiment"] == "context":
        overrides = {"context_tokens": protocol["context_budgets"][variant]}
    else:
        overrides = {"max_recovery": protocol["recovery_arms"][variant]}
    settings = settings.model_copy(update=overrides)
    runtime = (SilentRecovery if variant == "silent" else Runtime)(root, settings)
    runtime.start(task_spec(case))
    if case["experiment"] == "context":
        for message in case["history"]:
            runtime.add_message(**message)
        for message in read_data(protocol["history_file"])["messages"]:
            runtime.add_message(**message)
        setup = {
            "untrimmed": preview(runtime, 1_000_000),
            "selected": preview(runtime, settings.context_tokens),
        }
    else:
        policy = ScriptedPolicy(case["seed_actions"])
        policy.runtime = runtime
        runtime.model = policy
        for _ in case["seed_actions"]:
            await runtime.turn()
        stalls = [
            event["reason"]
            for event in runtime.evidence.events(runtime.state.run_id)
            if event["event"] == "stall_detected"
        ]
        if stalls != [case["expected_trigger"]]:
            raise ValueError(f"seed did not trigger the declared stall: {stalls}")
        setup = {
            "seed_steps": runtime.state.step,
            "seed_turns": runtime.state.turn,
            "trigger": stalls[0],
            "recovery_used": runtime._recovery_used,
        }
    setup["workspace_sha256"] = runtime.workspace.fingerprint()
    return runtime, setup


async def run_attempt(case, root, destination, settings, variant, protocol, *, model=None):
    runtime, setup = await initialize(case, root, settings, variant, protocol)
    destination.mkdir(parents=True, exist_ok=True)
    original = runtime.workspace.snapshot()
    original_text = runtime.workspace.text_snapshot()
    seed_steps, seed_turns = runtime.state.step, runtime.state.turn
    boundary = runtime.evidence.emit(runtime.state.run_id, "benchmark_handoff", setup=setup)
    observer = ObservedModel(
        runtime,
        model or ModelClient(runtime.settings),
        destination / "requests",
        case.get("history_evidence", []),
    )
    runtime.model = observer  # No reference action remains after handoff.
    started, error = time.monotonic(), ""
    try:
        await runtime.run()
    except Exception as exc:
        error = runtime.evidence.redactor.text(f"{type(exc).__name__}: {exc}")
        runtime.finish("evaluation_exception", failed=True)
    seconds = time.monotonic() - started
    try:
        checks = grade_contract(case, runtime, original, {})
    except Exception as exc:
        checks = {"grading_error": False}
        error = runtime.evidence.redactor.text(f"{type(exc).__name__}: {exc}")
    try:
        independent = await external_check(root, case["checks"]["verifier"])
    except Exception as exc:
        independent = {
            "passed": False,
            "grader_reason": runtime.evidence.redactor.text(f"{type(exc).__name__}: {exc}"),
        }
    checks["independent_tests"] = independent["passed"]
    if error:
        checks["runtime_error"] = False
    events = runtime.evidence.events(runtime.state.run_id)
    after = [event for event in events if event["seq"] > boundary["seq"]]
    contexts = [event for event in after if event["event"] == "context_built"]
    calls = [event for event in tool_events(runtime) if event["step"] > seed_steps]
    seed_keys = {action_key(action, runtime.workspace) for action in case.get("seed_actions", [])}
    first_call = calls[0]["call"] if calls else None
    first_key = action_key(first_call, runtime.workspace) if first_call else None
    evidence_paths = {runtime.workspace.path(path) for path in case.get("evidence_paths", [])}
    evidence_reads = 0
    for event in calls:
        if event["call"]["name"] == "read_file" and event["outcome"]["status"] == "success":
            evidence_reads += (
                runtime.workspace.path(event["call"]["arguments"]["path"]) in evidence_paths
            )
    row = {
        "task_id": case["id"],
        "base_task": case.get("base_task", case["id"]),
        "experiment": case["experiment"],
        "category": case["category"],
        "variant": variant,
        "passed": all(checks.values()),
        "checks": checks,
        "verifier": independent,
        "failure": error or ", ".join(key for key, ok in checks.items() if not ok),
        "stop_reason": runtime.state.stop_reason,
        "runtime_feedback": runtime.evidence.redactor.text(runtime.state.feedback),
        "setup": setup,
        **metrics(runtime, seconds),
        "autonomous_steps": runtime.state.step - seed_steps,
        "autonomous_turns": runtime.state.turn - seed_turns,
        "model_requests": len(observer.requests),
        "first_request": observer.requests[0] if observer.requests else None,
        "context_builds": len(contexts),
        "history_compactions": sum(event["removed_messages"] > 0 for event in contexts),
        "context_compactions": sum(event["compacted"] for event in contexts),
        "context_policy_versions": sorted({event["policy_version"] for event in contexts}),
        "removed_messages": sum(event["removed_messages"] for event in contexts),
        "all_within_budget": all(event["estimated_tokens"] <= event["budget"] for event in contexts)
        if contexts
        else None,
        "evidence_read_calls": evidence_reads,
        "observation_read_calls": sum(event["call"]["name"] == "observation_read" for event in calls),
        "observation_read_successes": sum(
            event["call"]["name"] == "observation_read" and event["outcome"]["status"] == "success"
            for event in calls
        ),
        "first_autonomous_call": first_call,
        "first_call_changed": first_key is not None and first_key not in seed_keys,
        "renewed_stalls": sum(event["event"] == "stall_detected" for event in after),
        "trace_path": None,
    }
    try:
        row["trace_path"] = archive(runtime, destination)
        (destination / "patch.diff").write_text(runtime.workspace.diff(original_text))
    except Exception as exc:
        row["passed"] = False
        row["checks"]["archive"] = False
        row["failure"] += runtime.evidence.redactor.text(f"; archive: {type(exc).__name__}: {exc}")
    save(destination / "result.json", row)
    return row


def summarize(rows):
    groups = defaultdict(list)
    for row in rows:
        groups[(row["experiment"], row["category"], row["variant"])].append(row)
    return {
        "/".join(key): {
            "scenarios": len({row["task_id"] for row in values}),
            "attempts": len(values),
            "passed": sum(row["passed"] for row in values),
            "mean_tokens_all": mean(row["total_tokens"] for row in values),
            "mean_steps_all": mean(row["autonomous_steps"] for row in values),
            "mean_seconds_all": mean(row["seconds"] for row in values),
            "history_compactions": sum(row.get("history_compactions", 0) for row in values),
            "renewed_stalls": sum(row.get("renewed_stalls", 0) for row in values),
            "evidence_read_calls": sum(row.get("evidence_read_calls", 0) for row in values),
            "observation_read_calls": sum(row.get("observation_read_calls", 0) for row in values),
        }
        for key, values in sorted(groups.items())
    }


def paired_summary(rows):
    pairs = defaultdict(dict)
    for row in rows:
        pairs[(row["experiment"], row["category"], row["task_id"], row["attempt"])][
            row["variant"]
        ] = row
    groups = defaultdict(list)
    for (experiment, category, _, _), arms in pairs.items():
        comparisons = (
            (("full", "balanced"), ("full", "tight"))
            if experiment == "context"
            else (("silent", "feedback"),)
        )
        for left, right in comparisons:
            if left in arms and right in arms:
                groups[(experiment, category, left, right)].append((arms[left], arms[right]))
    result = {}
    for key, values in sorted(groups.items()):
        common = [(a, b) for a, b in values if a["passed"] and b["passed"]]
        entry = {
            "pairs": len(values),
            "both_pass": len(common),
            "left_only": sum(a["passed"] and not b["passed"] for a, b in values),
            "right_only": sum(b["passed"] and not a["passed"] for a, b in values),
            "neither": sum(not a["passed"] and not b["passed"] for a, b in values),
        }
        for field in ("total_tokens", "autonomous_steps", "seconds", "evidence_read_calls"):
            entry["common_success_" + field] = (
                [mean(a[field] for a, _ in common), mean(b[field] for _, b in common)]
                if common
                else None
            )
        result["/".join(key)] = entry
    return result


def markdown(report):
    lines = [
        "# Aster 上下文与恢复专项评测",
        "",
        "预置历史/故障前缀，后续由真实模型独立执行；隐藏测试在结束后注入。",
        "恢复的主要对照是 silent 与 feedback：同样允许继续，只改变纠偏提示。stop 不计作提示收益。",
        "所有计划尝试（含网关失败）保留；三次重复不是三道新题。",
        "",
        "| 专项 / 类别 / 条件 | 场景 | 尝试 | 完成 | 平均后续工具调用 | 平均总 token | 平均秒 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for name, group in report["summary"].items():
        lines.append(
            f"| {name} | {group['scenarios']} | {group['attempts']} | {group['passed']} | "
            f"{group['mean_steps_all']:.2f} | {group['mean_tokens_all']:.0f} | {group['mean_seconds_all']:.2f} |"
        )
    lines += [
        "",
        "## 同题配对",
        "",
        "左右条件顺序见组名；成本仅在双方均成功的配对中比较，完整成功率见上表。",
        "",
        "| 类别 / 左 / 右 | 配对 | 都完成 | 仅左完成 | 仅右完成 | 都失败 | 共同成功平均 token 左→右 |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for name, group in report["pairs"].items():
        costs = group["common_success_total_tokens"]
        cost = f"{costs[0]:.0f} → {costs[1]:.0f}" if costs else "—"
        lines.append(
            f"| {name} | {group['pairs']} | {group['both_pass']} | {group['left_only']} | "
            f"{group['right_only']} | {group['neither']} | {cost} |"
        )
    lines += ["", "## 失败记录", ""]
    failures = [row for row in report["rows"] if not row["passed"]]
    for row in failures:
        lines.append(
            f"- {row['task_id']} / {row['variant']} / #{row['attempt']}: {row['stop_reason']}；{row['failure']}"
        )
    if not failures:
        lines.append("本批次没有任务失败。")
    lines += [
        "",
        "stop 的预期停止保留在失败列表中；它是控制组，不是运行时故障。",
        "输入缩减是本地估算；总 token 来自供应商 usage，不包含预置历史原本的生成成本。",
        "故障由实验注入，不据此推断自然运行中的停滞发生率。每次实际请求、执行记录、候选代码和隐藏评分均归档。",
        "",
    ]
    return "\n".join(lines)


async def preflight(cases, settings, protocol, output):
    rows, checked = [], set()
    with tempfile.TemporaryDirectory(prefix="aster-robustness-check-") as directory:
        for case in cases:
            base = case.get("base_task", case["id"])
            if base not in checked:
                baseline = await baseline_check(case)
                reference = await run_task(
                    case,
                    Path(directory) / f"reference-{case['id']}",
                    output / "references" / case["id"],
                    settings,
                )
                rows.append(
                    {
                        "id": base,
                        "kind": "grader",
                        "passed": baseline["fix_fails_before"]
                        and baseline["regression_passes_before"]
                        and reference["passed"],
                        "baseline": baseline,
                        "reference_passed": reference["passed"],
                    }
                )
                checked.add(base)
            variants = (
                protocol["context_budgets"]
                if case["experiment"] == "context"
                else protocol["recovery_arms"]
            )
            for variant in variants:
                runtime, setup = await initialize(
                    case, Path(directory) / case["id"] / variant, settings, variant, protocol
                )
                if case["experiment"] == "context":
                    selected = setup["selected"]
                    passed = selected["estimated_tokens"] <= selected["budget"] and (
                        variant == "full"
                        or selected["compacted"]
                        and setup["untrimmed"]["estimated_tokens"] > selected["budget"]
                    )
                else:
                    passed = runtime.state.terminal == (variant == "stop")
                rows.append(
                    {
                        "id": case["id"],
                        "variant": variant,
                        "kind": "setup",
                        "passed": passed,
                        "setup": setup,
                    }
                )
                await runtime.workers.close()
            print(f"preflight {case['id']}", flush=True)
    save(
        output / "preflight.json",
        {"source": source_identity(), "passed": all(row["passed"] for row in rows), "rows": rows},
    )
    return all(row["passed"] for row in rows)


async def evaluate(args):
    protocol = read_data("robustness_protocol.json")
    cases = load_cases(args.suite)
    if args.task:
        unknown = set(args.task) - {case["id"] for case in cases}
        if unknown:
            raise ValueError(f"unknown scenarios: {sorted(unknown)}")
        cases = [case for case in cases if case["id"] in args.task]
    settings = load_settings(Path(args.env_file), **protocol["settings"])
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    if not await preflight(cases, settings, protocol, output):
        raise ValueError("preflight failed; inspect preflight.json before calling the model")
    if not args.real:
        return 0
    if not settings.api_key.get_secret_value():
        raise ValueError("real evaluation requires the configured API key")
    repeats = args.repeats or protocol["repeats"]
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    jobs = []
    for case in cases:
        variants = (
            protocol["context_budgets"]
            if case["experiment"] == "context"
            else protocol["recovery_arms"]
        )
        for variant in variants:
            # Hard stopping is deterministic; do not spend three identical control attempts.
            for attempt in range(1, (1 if variant == "stop" else repeats) + 1):
                jobs.append((case, variant, attempt))
    random.Random(protocol["shuffle_seed"]).shuffle(jobs)
    report = {
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "source": source_identity(),
        "mode": "real",
        "settings": settings.identity(),
        "protocol": protocol,
        "dataset_sha256": dataset_identity(cases, protocol),
        "repeats": repeats,
        "manifest": [
            {"task_id": case["id"], "variant": variant, "attempt": attempt}
            for case, variant, attempt in jobs
        ],
        "rows": [],
    }
    archive_root = output / "runs" / stamp
    save(
        archive_root / "manifest.json",
        {key: value for key, value in report.items() if key != "rows"},
    )
    semaphore = asyncio.Semaphore(args.concurrency)
    with tempfile.TemporaryDirectory(prefix="aster-robustness-real-") as directory:

        async def one(case, variant, attempt):
            async with semaphore:
                relative = Path(case["id"]) / f"{variant}-{attempt}"
                try:
                    row = await run_attempt(
                        case,
                        Path(directory) / relative,
                        archive_root / relative,
                        settings,
                        variant,
                        protocol,
                    )
                except Exception as exc:
                    row = {
                        "task_id": case["id"],
                        "base_task": case.get("base_task", case["id"]),
                        "experiment": case["experiment"],
                        "category": case["category"],
                        "variant": variant,
                        "passed": False,
                        "failure": Redactor([settings.api_key.get_secret_value()]).text(
                            f"{type(exc).__name__}: {exc}"
                        ),
                        "stop_reason": "evaluation_setup_error",
                        "total_tokens": 0,
                        "autonomous_steps": 0,
                        "seconds": 0,
                        "evidence_read_calls": 0,
                    }
                    save(archive_root / relative / "result.json", row)
                row.update(attempt=attempt, batch_id=stamp)
                report["rows"].append(row)
                with (output / "attempts.jsonl").open("a") as stream:
                    stream.write(json.dumps(row, ensure_ascii=False) + "\n")
                print(
                    f"{len(report['rows'])}/{len(jobs)} {case['id']} {variant} #{attempt}: "
                    f"{'PASS' if row['passed'] else 'FAIL'} {row['stop_reason']} {row['failure'][:120]}",
                    flush=True,
                )

        await asyncio.gather(*(one(*job) for job in jobs))
    report["rows"].sort(
        key=lambda row: (row["experiment"], row["task_id"], row["variant"], row["attempt"])
    )
    report["summary"] = summarize(report["rows"])
    report["pairs"] = paired_summary(report["rows"])
    save(output / "report.json", report)
    save(archive_root / "report.json", report)
    (output / "report.md").write_text(markdown(report))
    print(f"Report: {output / 'report.md'}", flush=True)
    return int(any(not row["passed"] for row in report["rows"] if row["variant"] != "stop"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--real",
        action="store_true",
        help="Without this flag, check setup and independent graders only",
    )
    parser.add_argument("--suite", choices=["context", "recovery"])
    parser.add_argument("--task", action="append")
    parser.add_argument("--repeats", type=int)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--output", default="artifacts/benchmark-v5-robustness")
    args = parser.parse_args()
    if args.repeats is not None and args.repeats < 1 or not 1 <= args.concurrency <= 8:
        parser.error("repeats >= 1; concurrency 1..8")
    return asyncio.run(evaluate(args))


if __name__ == "__main__":
    raise SystemExit(main())
