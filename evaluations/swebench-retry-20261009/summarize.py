"""Export five retries and combine their official grades with 25 unchanged attempts."""
from __future__ import annotations

import gzip
import hashlib
import json
import shutil
from pathlib import Path

from rerun import JOBS, ORIGINAL, PRIVATE

HERE = Path(__file__).resolve().parent
AGENTS = ("aster", "pi", "codex")


def read(path):
    return json.loads(path.read_text())


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def export():
    original_manifest = read(ORIGINAL / "manifest.json")
    tasks = {r["instance_id"]: r for r in original_manifest["instances"]}
    updated = {}
    for agent, instance in JOBS:
        root = PRIVATE / "runs" / f"{agent}-{instance}"
        if not (root / "result.json").exists():
            continue
        result = read(root / "result.json")
        patch = (root / "prediction.patch").read_bytes()
        assert hashlib.sha256(patch).hexdigest() == result["patch_sha256"]
        launch = read(root / "launch.json")
        previous = read(ORIGINAL / "results" / agent / (instance + ".launch.json"))
        for key in ("model", "reasoning_effort", "seconds_limit", "context_tokens", "output_tokens", "prompt", "image_id", "runner_sha256"):
            assert launch[key] == previous[key], (instance, key)
        metadata = {f.stem: read(f) for f in (root / "provider").glob("*.json") if not f.name.endswith(".request.json")}
        for f in (root / "provider").glob("*.request.json"):
            key = f.name.removesuffix(".request.json")
            metadata.setdefault(key, {"request_id": key, "usage": None, "error": "metadata_missing"})
        usage = [r["usage"] for r in metadata.values() if r.get("usage")]
        grade = PRIVATE / "logs/evaluation" / f"retry-{agent}-20261009" / f"{agent}-gpt-5.6-luna" / instance / "report.json"
        result.update(
            difficulty=tasks[instance]["aster_difficulty"],
            requests=len(metadata), requests_without_usage=len(metadata) - len(usage),
            failed_requests=sum(bool(r.get("error")) or r.get("http_status", 200) >= 400
                                or r.get("terminal") == "response.failed" for r in metadata.values()),
            known_tokens=sum(r.get("total_tokens", 0) for r in usage),
            known_input_tokens=sum(r.get("input_tokens", 0) for r in usage),
            known_output_tokens=sum(r.get("output_tokens", 0) for r in usage),
            known_cached_tokens=sum(r.get("input_tokens_details", {}).get("cached_tokens", 0) for r in usage),
            graded=grade.exists() or not patch,
            resolved=read(grade)[instance]["resolved"] if grade.exists() else False,
            grading_status="reported" if grade.exists() else ("empty_patch" if not patch else "pending"),
        )
        target = HERE / "results" / agent
        save(target / (instance + ".json"), result)
        save(target / (instance + ".launch.json"), launch)
        save(target / (instance + ".usage.json"), [dict(r, usage={k: v for k, v in r["usage"].items() if k != "attribution"})
                                                if r.get("usage") else r for r in metadata.values()])
        (target / (instance + ".patch")).write_bytes(patch)
        (target / (instance + ".agent.jsonl.gz")).write_bytes(gzip.compress((root / "agent.jsonl").read_bytes(), mtime=0))
        if grade.exists():
            shutil.copy2(grade, target / (instance + ".official.json"))
            (target / (instance + ".tests.txt.gz")).write_bytes(gzip.compress((grade.parent / "test_output.txt").read_bytes(), mtime=0))
        updated[agent, instance] = result

    summary = {"planned_retries": 5, "completed_retries": len(updated), "graded_retries": sum(r["graded"] for r in updated.values()), "agents": {}}
    complete = summary["graded_retries"] == 5
    lines = ["# SWE-bench 五次故障补测", "", "2026-10-09。同模型 gpt-5.6-luna，同源码、镜像、提示词与预算，只补原轮次五次故障尝试。", "",
             "| Agent | 原始十题 | 补测通过 | 补测后十题 | 简单 | 中等 | 困难 |", "|---|---:|---:|---:|---:|---:|---:|"]
    for agent in AGENTS:
        retry_predictions, combined_predictions, combined_results = [], [], []
        original_predictions = {r["instance_id"]: r for r in map(json.loads, (ORIGINAL / (agent + ".predictions.jsonl")).read_text().splitlines())}
        for instance in tasks:
            row = updated.get((agent, instance))
            prediction = original_predictions[instance]
            if row:
                prediction = dict(prediction, model_patch=(PRIVATE / "runs" / row["job"] / "prediction.patch").read_text())
                retry_predictions.append(prediction)
            else:
                row = read(ORIGINAL / "results" / agent / (instance + ".json"))
            combined_predictions.append(prediction)
            combined_results.append(dict(row, result_source="retry" if (agent, instance) in updated else "original"))
        (HERE / (agent + ".predictions.jsonl")).write_text("".join(json.dumps(r) + "\n" for r in retry_predictions))
        if complete:
            combined = HERE / "combined"
            combined.mkdir(exist_ok=True)
            (combined / (agent + ".predictions.jsonl")).write_text("".join(json.dumps(r) + "\n" for r in combined_predictions))
            save(combined / (agent + ".results.json"), combined_results)
        aggregate = PRIVATE / "logs/evaluation" / f"retry-{agent}-20261009" / "results.json"
        if aggregate.exists():
            shutil.copy2(aggregate, HERE / "results" / agent / "official-summary.json")
        original = read(ORIGINAL / "summary.json")["agents"][agent]
        retries = [r for (a, _), r in updated.items() if a == agent]
        counts = {"original_resolved": original["resolved"], "retried": len(retries),
                  "retry_resolved": sum(r["resolved"] for r in retries),
                  "combined_resolved": sum(r["resolved"] for r in combined_results) if complete else None,
                  "retry_known_tokens": sum(r["known_tokens"] for r in retries),
                  "retry_requests_without_usage": sum(r["requests_without_usage"] for r in retries),
                  "retry_failed_requests": sum(r["failed_requests"] for r in retries),
                  "retry_seconds": round(sum(r["seconds"] for r in retries), 3),
                  "total_spent_known_tokens": original["known_tokens"] + sum(r["known_tokens"] for r in retries),
                  "difficulty": {d: sum(r["resolved"] for r in combined_results if r["difficulty"] == d)
                                 for d in ("simple", "medium", "hard")} if complete else None}
        summary["agents"][agent] = counts
        budget = sum(a == agent for a, _ in JOBS)
        score = f"{counts['combined_resolved']}/10（{counts['combined_resolved'] * 10}%）" if complete else "待完成"
        difficulty = " | ".join(f"{counts['difficulty'][d]}/{n}" for d, n in (("simple", 3), ("medium", 5), ("hard", 2))) if complete else "待完成 | 待完成 | 待完成"
        lines.append(f"| {agent} | {original['resolved']}/10（{original['resolved'] * 10}%） | {counts['retry_resolved']}/{budget} | {score} | {difficulty} |")
    lines += ["", "补测后十题使用五次补测结果替换原故障尝试，其余 25 次结果原样沿用；不是完整重跑，也不取两次最好成绩。", "",
              "| Agent | 补测题目 | 官方结果 | 用时（秒） | 已报告 token | 请求异常数 |", "|---|---|---|---:|---:|---:|"]
    for agent, instance in JOBS:
        r = updated.get((agent, instance))
        if r:
            status = ("通过" if r["resolved"] else "未通过") if r["graded"] else "待评分"
            lines.append(f"| {agent} | {instance} | {status} | {r['seconds']:.1f} | {r['known_tokens']:,} | {r['failed_requests']} |")
    lines += ["", "| Agent | 补测新增 token | 原轮次＋补测累计 token | 补测未知用量请求 |", "|---|---:|---:|---:|"]
    for agent, r in summary["agents"].items():
        lines.append(f"| {agent} | {r['retry_known_tokens']:,} | {r['total_spent_known_tokens']:,} | {r['retry_requests_without_usage']} |")
    lines += ["", "token 按供应商已报告用量记录，原失败尝试的消耗没有删去，未知用量不视为零费用。没有该供应商的计费单价，不换算货币成本；另有一次连通性检查，见 preflight.json。", "",
              "五次补测都留下了非空补丁。pi 的 django-15957 达到 900 秒上限，仍用当时的补丁评分；其余四次正常结束。没有再次出现原轮次的持续 ConnectError 或主机长时间卡顿。两次 BrokenPipe 分别发生在 Aster 收尾中止后台记忆整理、pi 到达时间上限时，用量缺失仍保留。", "",
              "五份补丁均成功应用，官方评分阶段的基础设施失败与执行错误均为 0。Codex 的 django-15382 通过目标修复测试 test_negated_empty_exists，已有测试也全部通过。", "",
              "Aster 的两次补测都存在实际修复遗漏：sphinx-9658 的 test_restify_mock 仍失败，mock 对象被生成了错误引用；sympy-13031 的 test_sparse_matrix 仍失败，空稀疏矩阵横向拼接的列数不符合预期。pi 的 django-15957 有四条目标修复测试未通过，并破坏了一条已有测试；sympy-18199 的 test_solve_modular 仍失败。对应证据保存在逐题官方报告及测试日志中。", "",
              "分数仍是官方评分器下的十题子集结果，不能视为完整榜单或稳定排名；其他原始尝试中的网络波动、超时和选题限制仍然保留。", "",
              "[原始报告](../swebench-compare-20261008/REPORT.md) · [补测协议](PROTOCOL.md)。本轮预测和官方报告见 results/；combined/ 保存补测后十题预测与各题来源。", ""]
    save(HERE / "summary.json", summary)
    (HERE / "REPORT.md").write_text("\n".join(lines))
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    export()
