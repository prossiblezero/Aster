"""Deterministic module-level benchmarks for Aster's Harness mechanisms.

The benchmark uses no remote model. Scripted policies make the causal question
explicit: each treatment is compared with a deliberately specified baseline,
and every claim is backed by a physical artifact or a verifier result.
"""

from __future__ import annotations

import asyncio
import copy
import json
import shutil
import tempfile
from pathlib import Path

from evaluate import harness_cases

from aster.config import Settings
from aster.context import ContextBuilder
from aster.memory import Memory
from aster.recovery import StallDetector
from aster.runtime import Runtime
from aster.schema import ModelReply, Outcome, RunState, TaskSpec, ToolCall


class FakeModel:
    def __init__(self, replies):
        self.replies = iter(replies)

    async def complete(self, _messages, _tools):
        return next(self.replies)


def reply(*calls, text=""):
    return ModelReply(
        text=text,
        calls=list(calls),
        raw=[{"role": "assistant", "content": text}] if text else [],
    )


def ratio(numerator, denominator):
    return numerator / denominator if denominator else 0.0


def mean(values):
    values = list(values)
    return sum(values) / len(values) if values else 0.0


def context_benchmark():
    rows = []
    for history_size in (600, 1200, 2400, 4800):
        for memory_size in (0, 600, 1200):
            state = RunState(task=TaskSpec(prompt="current request: preserve this exact task"))
            state.messages = [
                {"role": "assistant", "content": "ordinary history " + ("x" * history_size)},
                {
                    "role": "tool",
                    "content": "important verification evidence: keep-this-marker",
                    "important": True,
                },
            ]
            memory = "memory marker: " + ("m" * memory_size) if memory_size else ""
            raw_state = copy.deepcopy(state)
            raw = ContextBuilder(100_000, 128)
            raw_prompt, raw_report = raw.build(raw_state, state.task.prompt, memory)
            compact_state = copy.deepcopy(state)
            compact = ContextBuilder(1024, 128)
            prompt, report = compact.build(compact_state, state.task.prompt, memory)
            rendered = json.dumps(prompt, ensure_ascii=False)
            rows.append(
                {
                    "history_size": history_size,
                    "memory_size": memory_size,
                    "raw_tokens": raw_report.estimated_tokens,
                    "compact_tokens": report.estimated_tokens,
                    "compression_ratio": 1 - ratio(report.estimated_tokens, raw_report.estimated_tokens),
                    "within_budget": report.estimated_tokens <= report.budget,
                    "request_preserved": "current request: preserve this exact task" in rendered,
                    "important_evidence_preserved": "keep-this-marker" in rendered,
                    "compacted": report.compacted,
                }
            )
    return {
        "task_count": len(rows),
        "rows": rows,
        "summary": {
            "avg_raw_tokens": mean(x["raw_tokens"] for x in rows),
            "avg_compact_tokens": mean(x["compact_tokens"] for x in rows),
            "avg_compression_ratio": mean(x["compression_ratio"] for x in rows),
            "max_compression_ratio": max(x["compression_ratio"] for x in rows),
            "budget_compliance_rate": ratio(sum(x["within_budget"] for x in rows), len(rows)),
            "request_retention_rate": ratio(sum(x["request_preserved"] for x in rows), len(rows)),
            "important_evidence_retention_rate": ratio(
                sum(x["important_evidence_preserved"] for x in rows), len(rows)
            ),
            "compaction_activation_rate": ratio(sum(x["compacted"] for x in rows), len(rows)),
        },
    }


async def memory_benchmark(root: Path):
    cases = [
        ("deploy_color", "deploy key color", "deploy key is red"),
        ("api_path", "internal api path", "api base path is /v1/internal"),
        ("step_budget", "default step budget", "default step budget is 6"),
        ("timeout", "process timeout ceiling", "timeout ceiling is 120 seconds"),
        ("health_route", "health endpoint route", "health endpoint is /healthz"),
        ("cache_ttl", "cache ttl", "cache ttl is 60 seconds"),
        ("db_port", "database port", "database port is 5432"),
        ("log_level", "production log level", "production log level is warning"),
        ("retry_limit", "retry limit", "retry limit is 3"),
        ("worker_scope", "worker write scope", "worker write scope is src"),
        ("image_format", "image format", "image format is webp"),
        ("release_branch", "release branch", "release branch is main"),
    ]
    rows = []
    for case_id, query, fact in cases:
        case_root = root / case_id
        memory = Memory(case_root)
        memory.record(fact, run_id=case_id, evidence_id=f"e-{case_id}", tags=query.split())
        for index in range(5):
            memory.record(
                f"distractor {index} unrelated repository note",
                run_id=case_id,
                evidence_id=f"d-{case_id}-{index}",
                tags=["unrelated"],
            )
        selected = memory.retrieve(query, limit=3)
        texts = [item["text"] for item in selected]
        rank = next((index + 1 for index, text in enumerate(texts) if text == fact), None)
        rows.append(
            {
                "case": case_id,
                "relevant_found": rank is not None,
                "rank": rank,
                "precision_at_3": ratio(sum(fact == text for text in texts), len(texts)),
                "irrelevant_in_top3": sum(fact != text for text in texts),
            }
        )

    evolution = Memory(root / "evolution")
    supported = evolution.record(
        "run tests after edits", run_id="evolution", evidence_id="support-1", tags=["tests"]
    )
    evolution.record(
        "run tests after edits", run_id="evolution", evidence_id="support-2", tags=["tests"]
    )
    contradicted = evolution.record(
        "run tests after edits",
        run_id="evolution",
        evidence_id="failure-1",
        outcome="contradiction",
    )
    rejected = evolution.record("forged", run_id="evolution", evidence_id="")
    snapshot = evolution.snapshot()
    evolution.record("temporary lesson", run_id="evolution", evidence_id="temporary")
    rollback_ok = evolution.rollback(snapshot)
    lesson = next(item for item in evolution.state["lessons"] if item["text"] == "run tests after edits")
    evolution.record_use([lesson["id"]], success=True)
    evolution.dream()
    evolution_metrics = {
        "support_count": lesson["support"],
        "contradiction_count": lesson["contradictions"],
        "provenance_rejection": rejected is None,
        "snapshot_rollback": rollback_ok
        and not any(item["text"] == "temporary lesson" for item in evolution.state["lessons"]),
        "successful_use_feedback": lesson["successful_uses"] == 1,
        "dedup_after_dream": len([item for item in evolution.state["lessons"] if item["text"] == "run tests after edits"]) == 1,
        "recorded_support": supported is not None and contradicted is not None,
    }

    # Paired dependency task through the real Runtime: the scripted policy reads
    # when the recalled lesson is absent and answers directly when it is present.
    dependency_rows = []
    for index in range(12):
        baseline_root = root / f"dependency-baseline-{index}"
        treatment_root = root / f"dependency-memory-{index}"
        for case_root in (baseline_root, treatment_root):
            case_root.mkdir(parents=True, exist_ok=True)
            (case_root / "facts.txt").write_text("deploy key is red\n")

        class DependencyModel:
            def __init__(self):
                self.seen = False

            async def complete(self, messages, _tools):
                rendered = json.dumps(messages, ensure_ascii=False).casefold()
                if "deploy key is red" in rendered:
                    return reply(text="deploy key is red")
                if not self.seen:
                    self.seen = True
                    return reply(ToolCall(name="read_file", arguments={"path": "facts.txt"}))
                return reply(text="deploy key is red")

        baseline_runtime = Runtime(
            baseline_root,
            Settings(approval="auto", memory_enabled=False, completion="off"),
            model=DependencyModel(),
        )
        baseline_state = await baseline_runtime.run(
            TaskSpec(prompt="What color is the deploy key?", max_steps=4)
        )
        treatment_memory = Memory(treatment_root)
        treatment_memory.record(
            "deploy key is red",
            run_id=f"dependency-{index}",
            evidence_id=f"dependency-evidence-{index}",
            tags=["deploy", "key", "color"],
        )
        treatment_runtime = Runtime(
            treatment_root,
            Settings(approval="auto", memory_enabled=True, completion="off"),
            model=DependencyModel(),
        )
        treatment_state = await treatment_runtime.run(
            TaskSpec(prompt="What color is the deploy key?", max_steps=4)
        )
        baseline_reads = sum(
            event["event"] == "tool_result"
            and event.get("call", {}).get("name") == "read_file"
            for event in baseline_runtime.evidence.events(baseline_state.run_id)
        )
        treatment_reads = sum(
            event["event"] == "tool_result"
            and event.get("call", {}).get("name") == "read_file"
            for event in treatment_runtime.evidence.events(treatment_state.run_id)
        )
        dependency_rows.append(
            {
                "case": index,
                "baseline_reads": baseline_reads,
                "memory_reads": treatment_reads,
                "baseline_completed": baseline_state.phase == "completed",
                "memory_completed": treatment_state.phase == "completed",
            }
        )
    baseline_reads = sum(row["baseline_reads"] for row in dependency_rows)
    memory_reads = sum(row["memory_reads"] for row in dependency_rows)
    return {
        "task_count": len(rows),
        "rows": rows,
        "dependency_rows": dependency_rows,
        "summary": {
            "recall_at_3": ratio(sum(row["relevant_found"] for row in rows), len(rows)),
            "mean_reciprocal_rank": mean(1 / row["rank"] for row in rows if row["rank"]),
            "mean_precision_at_3": mean(row["precision_at_3"] for row in rows),
            "irrelevant_top3_rate": ratio(sum(row["irrelevant_in_top3"] > 0 for row in rows), len(rows)),
            "followup_repeated_reads_baseline": baseline_reads,
            "followup_repeated_reads_memory": memory_reads,
            "followup_read_reduction": 1 - ratio(memory_reads, baseline_reads),
            "evolution": evolution_metrics,
        },
    }


async def recovery_benchmark(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    rows = []
    for index in range(10):
        case_root = root / f"resume-{index}"
        case_root.mkdir(parents=True)
        (case_root / "app.py").write_text("print('stable')\n")
        runtime = Runtime(case_root, Settings(approval="auto"))
        state = runtime.start(TaskSpec(prompt="inspect app.py"))
        runtime.checkpoint()
        resumed = Runtime(case_root, Settings(approval="auto"))
        resume_ok = resumed.resume(state.run_id).run_id == state.run_id
        (case_root / "app.py").write_text("print('drifted')\n")
        drift_rejected = False
        try:
            resumed.resume(state.run_id)
        except ValueError as exc:
            drift_rejected = str(exc) == "workspace_mismatch"
        identity_rejected = False
        try:
            Runtime(case_root, Settings(approval="never")).resume(state.run_id)
        except ValueError as exc:
            identity_rejected = str(exc) == "runtime_identity_mismatch"
        rows.append(
            {
                "case": index,
                "resume_success": resume_ok,
                "workspace_drift_rejected": drift_rejected,
                "identity_mismatch_rejected": identity_rejected,
            }
        )

    stall_cases = {
        "repeated_identical": [
            ("read_file", {"path": "a"}, "same"),
            ("read_file", {"path": "a"}, "same"),
        ],
        "oscillation": [
            ("read_file", {"path": "a"}, "a"),
            ("search", {"pattern": "a"}, "b"),
            ("read_file", {"path": "a"}, "a"),
            ("search", {"pattern": "a"}, "b"),
        ],
        "output_loop": [
            ("read_file", {"path": "a"}, "same"),
            ("search", {"pattern": "a"}, "same"),
            ("list_files", {}, "same"),
        ],
    }
    detected = {}
    for expected, sequence in stall_cases.items():
        detector = StallDetector()
        reason = ""
        for name, arguments, content in sequence:
            reason = detector.observe(
                ToolCall(name=name, arguments=arguments),
                Outcome(status="success", content=content),
            )
        detected[expected] = reason == expected

    recovery_root = root / "bounded"
    recovery_root.mkdir(parents=True, exist_ok=True)
    runtime = Runtime(recovery_root, Settings(approval="auto", max_recovery=1))
    runtime.start(TaskSpec(prompt="inspect", max_steps=8))
    repeated = ToolCall(name="list_files", arguments={}, call_id="same-call")
    await runtime.step(repeated)
    await runtime.step(repeated)
    await runtime.step(repeated)
    bounded_stop = runtime.state.stop_reason == "stall_recovery_exhausted"
    return {
        "task_count": len(rows),
        "rows": rows,
        "summary": {
            "resume_success_rate": ratio(sum(row["resume_success"] for row in rows), len(rows)),
            "workspace_drift_rejection_rate": ratio(
                sum(row["workspace_drift_rejected"] for row in rows), len(rows)
            ),
            "identity_mismatch_rejection_rate": ratio(
                sum(row["identity_mismatch_rejected"] for row in rows), len(rows)
            ),
            "stall_detection": detected,
            "stall_detection_rate": ratio(sum(detected.values()), len(detected)),
            "bounded_recovery_budget": Settings().max_recovery,
            "bounded_recovery_stops": bounded_stop,
        },
    }


async def completion_benchmark(root: Path):
    rows = []
    for index in range(3):
        case_root = root / f"completion-{index}"
        case_root.mkdir(parents=True)
        (case_root / "app.py").write_text("print('old')\n")
        (case_root / "test_app.py").write_text("def test_ok():\n    assert True\n")
        (case_root / "app.py").write_text("print('old')\n")
        runtime = Runtime(case_root, Settings(approval="auto", sandbox="required"))
        runtime.start(TaskSpec(prompt="Implement a change in app.py and run tests"))
        state = runtime.finalize("done")
        while state.phase == "running":
            state = runtime.finalize("done")
        rows.append(
            {
                "case": "final_without_edit",
                "expected_phase": "stopped",
                "actual_phase": state.phase,
                "contract_enforced": state.phase == "stopped",
            }
        )

        (case_root / "app.py").write_text("print('new')\n")
        runtime = Runtime(case_root, Settings(approval="auto", sandbox="required"))
        state = runtime.start(TaskSpec(prompt="Implement a change in app.py and run tests"))
        await runtime.step(ToolCall(name="read_file", arguments={"path": "app.py"}))
        await runtime.step(
            ToolCall(name="patch_file", arguments={"path": "app.py", "old_text": "old", "new_text": "new"})
        )
        state = runtime.finalize("done")
        while state.phase == "running":
            state = runtime.finalize("done")
        rows.append(
            {
                "case": "edit_without_verification",
                "expected_phase": "stopped",
                "actual_phase": state.phase,
                "contract_enforced": state.phase == "stopped",
            }
        )

        runtime = Runtime(case_root, Settings(approval="auto", sandbox="required"))
        state = runtime.start(TaskSpec(prompt="Implement a change in app.py and run tests"))
        await runtime.step(ToolCall(name="read_file", arguments={"path": "app.py"}))
        await runtime.step(
            ToolCall(name="patch_file", arguments={"path": "app.py", "old_text": "new", "new_text": "final"})
        )
        await runtime.step(ToolCall(name="run_tests", arguments={"command": "python -m pytest -q"}))
        state = runtime.finalize("done")
        rows.append(
            {
                "case": "edit_verify_final",
                "expected_phase": "completed",
                "actual_phase": state.phase,
                "contract_enforced": state.phase == "completed",
            }
        )
    return {
        "task_count": len(rows),
        "rows": rows,
        "summary": {
            "completion_contract_enforcement_rate": ratio(
                sum(row["contract_enforced"] for row in rows), len(rows)
            )
        },
    }


async def main():
    output = Path("artifacts")
    output.mkdir(exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="aster-module-benchmark-"))
    try:
        harness = await harness_cases()
        payload = {
            "suite": "aster-module-benchmark-v1",
            "method": {
                "model": "scripted-deterministic",
                "remote_model": False,
                "baselines": {
                    "context": "same messages rendered with a large budget (no compaction)",
                    "memory": "scripted follow-up policy without recalled lesson",
                    "recovery": "checkpoint/resume and stall detector contract cases",
                },
                "repetitions": {"context_matrix": 12, "memory_retrieval": 12, "recovery": 10, "completion": 9},
            },
            "harness_regression": {
                "task_count": len(harness),
                "passed": sum(row["passed"] for row in harness),
                "pass_rate": ratio(sum(row["passed"] for row in harness), len(harness)),
            },
            "context": context_benchmark(),
            "memory": await memory_benchmark(root / "memory-cases"),
            "recovery": await recovery_benchmark(root / "recovery-cases"),
            "completion": await completion_benchmark(root / "completion-cases"),
        }
    finally:
        shutil.rmtree(root, ignore_errors=True)
    (output / "module-benchmark-v1.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    summary = payload
    context = summary["context"]["summary"]
    memory = summary["memory"]["summary"]
    recovery = summary["recovery"]["summary"]
    completion = summary["completion"]["summary"]
    markdown = f"""# Aster module benchmark v1

该报告只统计确定性 scripted benchmark，不代表远程模型成功率。

## 总体

- Harness regression：{summary['harness_regression']['passed']}/{summary['harness_regression']['task_count']}（{summary['harness_regression']['pass_rate']:.0%}）
- Context cases：{summary['context']['task_count']}
- Memory retrieval cases：{summary['memory']['task_count']}
- Recovery cases：{summary['recovery']['task_count']}
- Completion cases：{summary['completion']['task_count']}

## Context

- 平均 token：{context['avg_raw_tokens']:.1f} → {context['avg_compact_tokens']:.1f}
- 平均压缩率：{context['avg_compression_ratio']:.2%}
- 最高压缩率：{context['max_compression_ratio']:.2%}
- 预算内率：{context['budget_compliance_rate']:.2%}
- 当前请求保留率：{context['request_retention_rate']:.2%}
- 重要验证证据保留率：{context['important_evidence_retention_rate']:.2%}

## Memory

- Recall@3：{memory['recall_at_3']:.2%}
- MRR：{memory['mean_reciprocal_rank']:.3f}
- Top-3 平均 precision：{memory['mean_precision_at_3']:.2%}
- 含无关项的 Top-3 比例：{memory['irrelevant_top3_rate']:.2%}
- follow-up 重复读取：{memory['followup_repeated_reads_baseline']} → {memory['followup_repeated_reads_memory']}（减少 {memory['followup_read_reduction']:.0%}）
- Evolution：{json.dumps(memory['evolution'], ensure_ascii=False)}

## Recovery and completion

- checkpoint resume 成功率：{recovery['resume_success_rate']:.2%}
- workspace drift 拒绝率：{recovery['workspace_drift_rejection_rate']:.2%}
- runtime identity mismatch 拒绝率：{recovery['identity_mismatch_rejection_rate']:.2%}
- stall pattern 检出率：{recovery['stall_detection_rate']:.2%}
- 有界恢复耗尽后停止：{recovery['bounded_recovery_stops']}
- completion contract enforcement rate：{completion['completion_contract_enforcement_rate']:.2%}

## 解释边界

这些结果证明 Aster 的机制在固定输入和物理证据下满足契约。Memory follow-up 是 scripted policy
对照，不是 GPT 成功率；召回实验使用固定关键词 query，不代表开放域语义检索。当前实现也没有
文件新鲜度驱动的陈旧记忆拒绝。真实模型仍需单独运行、多次重复，并报告模型、温度、任务集和失败样本。
"""
    (output / "module-benchmark-v1.md").write_text(markdown)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
