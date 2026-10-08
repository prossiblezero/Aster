"""Export completed attempts and summarize official reports without model calls."""
from __future__ import annotations

import gzip
import hashlib
import json
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
PRIVATE = HERE.parents[1] / "artifacts/private" / HERE.name
AGENTS = ("aster", "pi", "codex")


def main():
    manifest = json.loads((HERE / "manifest.json").read_text())
    tasks = {t["instance_id"]: t for t in manifest["instances"]}
    rows = []
    for agent in AGENTS:
        predictions = []
        for instance in tasks:
            root = PRIVATE / "runs" / f"{agent}-{instance}"
            if not (root / "result.json").exists():
                continue
            result = json.loads((root / "result.json").read_text())
            patch = (root / "prediction.patch").read_bytes()
            assert hashlib.sha256(patch).hexdigest() == result["patch_sha256"]
            predictions.append({"instance_id": instance, "model_name_or_path": agent + "-gpt-5.6-luna",
                                "model_patch": patch.decode()})
            observations = {p.stem: json.loads(p.read_text()) for p in (root / "provider").glob("*.json")
                            if not p.name.endswith(".request.json")}
            for request in (root / "provider").glob("*.request.json"):
                key = request.name.removesuffix(".request.json")
                observations.setdefault(key, {"request_id": key, "usage": None, "error": "metadata_missing"})
            observations = list(observations.values())
            usages = [r["usage"] for r in observations if r.get("usage")]
            result.update(difficulty=tasks[instance]["aster_difficulty"],
                          requests=len(observations), requests_with_usage=len(usages),
                          failed_requests=sum(bool(r.get("error")) or r.get("http_status", 200) >= 400
                                              or r.get("terminal") == "response.failed" for r in observations),
                          known_tokens=sum(r.get("total_tokens", 0) for r in usages),
                          known_input_tokens=sum(r.get("input_tokens", 0) for r in usages),
                          known_output_tokens=sum(r.get("output_tokens", 0) for r in usages),
                          known_cached_tokens=sum(r.get("input_tokens_details", {}).get("cached_tokens", 0) for r in usages))
            grade = PRIVATE / "logs/evaluation" / f"comparison-{agent}-20261008" / f"{agent}-gpt-5.6-luna" / instance / "report.json"
            result["graded"] = grade.exists() or not patch
            result["resolved"] = json.loads(grade.read_text())[instance]["resolved"] if grade.exists() else False
            result["grading_status"] = "reported" if grade.exists() else ("empty_patch" if not patch else "pending_or_error")
            target = HERE / "results" / agent
            target.mkdir(parents=True, exist_ok=True)
            (target / (instance + ".json")).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
            public_usage = [dict(r, usage={k: v for k, v in r["usage"].items() if k != "attribution"})
                            if r.get("usage") else r for r in observations]
            (target / (instance + ".usage.json")).write_text(json.dumps(public_usage, indent=2) + "\n")
            shutil.copy2(root / "launch.json", target / (instance + ".launch.json"))
            evidence = HERE / "predictions" / agent
            evidence.mkdir(parents=True, exist_ok=True)
            (evidence / (instance + ".patch")).write_bytes(patch)
            (evidence / (instance + ".jsonl.gz")).write_bytes(gzip.compress((root / "agent.jsonl").read_bytes(), mtime=0))
            if grade.exists():
                shutil.copy2(grade, target / (instance + ".official.json"))
                test_output = grade.parent / "test_output.txt"
                if test_output.exists():
                    (target / (instance + ".tests.txt.gz")).write_bytes(gzip.compress(test_output.read_bytes(), mtime=0))
            rows.append(result)
        (HERE / (agent + ".predictions.jsonl")).write_text("".join(json.dumps(r) + "\n" for r in predictions))
        aggregate = PRIVATE / "logs/evaluation" / f"comparison-{agent}-20261008" / "results.json"
        if aggregate.exists():
            shutil.copy2(aggregate, HERE / "results" / agent / "official-summary.json")
    summary = {"planned": 30, "completed": len(rows), "graded": sum(r["graded"] for r in rows), "agents": {}}
    lines = ["# SWE-bench Verified 十题：Aster / pi / Codex", "",
             "2026-10-08。固定十题，每种 Agent 每题一次，官方评分器与官方 Docker 镜像。", "",
             "同模型 gpt-5.6-luna，reasoning effort=medium，64000 上下文，每题时间上限设为 900 秒；一次主机卡顿导致终止延迟，见下文。", "",
             "| Agent | 已执行 | 已评分 | 解决题数 | 简单 | 中等 | 困难 | 已知 token | 未知用量请求 | 秒合计 |",
             "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for agent in AGENTS:
        group = [r for r in rows if r["agent"] == agent]
        counts = {"completed": len(group), "graded": sum(r["graded"] for r in group), "resolved": sum(r["resolved"] for r in group),
                  "known_tokens": sum(r["known_tokens"] for r in group),
                  "known_input_tokens": sum(r["known_input_tokens"] for r in group),
                  "known_output_tokens": sum(r["known_output_tokens"] for r in group),
                  "known_cached_tokens": sum(r["known_cached_tokens"] for r in group),
                  "failed_requests": sum(r["failed_requests"] for r in group),
                  "tasks_with_request_errors": sum(r["failed_requests"] > 0 for r in group),
                  "timed_out": sum(r["timed_out"] for r in group),
                  "requests_without_usage": sum(r["requests"] - r["requests_with_usage"] for r in group),
                  "seconds": round(sum(r["seconds"] for r in group), 3), "difficulty": {}}
        for level in ("simple", "medium", "hard"):
            counts["difficulty"][level] = sum(r["resolved"] for r in group if r["difficulty"] == level)
        counts["resolved_rate_percent"] = counts["resolved"] * 10 if counts["graded"] == 10 else None
        summary["agents"][agent] = counts
        d = counts["difficulty"]
        score = f"{counts['resolved']}/10（{counts['resolved_rate_percent']}%）" if counts["graded"] == 10 else "待评分"
        lines.append(f"| {agent} | {len(group)}/10 | {counts['graded']}/10 | {score} | {d['simple']}/3 | {d['medium']}/5 | {d['hard']}/2 | {counts['known_tokens']:,} | {counts['requests_without_usage']} | {counts['seconds']:.1f} |")
    lines += ["", "| Agent | 已报告输入 token | 其中缓存 token | 未缓存输入 token | 输出 token |", "|---|---:|---:|---:|---:|"]
    for agent, counts in summary["agents"].items():
        incoming, cached = counts["known_input_tokens"], counts["known_cached_tokens"]
        lines.append(f"| {agent} | {incoming:,} | {cached:,} | {incoming - cached:,} | {counts['known_output_tokens']:,} |")
    lines += ["", "token 用量按供应商返回统计；没有该供应商的计费单价，因此不换算货币成本。缓存 token 占比不等于请求缓存命中率。", "",
              "| Agent | 超时题数 | 遇到请求异常的题数 | 异常请求数 |", "|---|---:|---:|---:|"]
    for agent, counts in summary["agents"].items():
        lines.append(f"| {agent} | {counts['timed_out']} | {counts['tasks_with_request_errors']} | {counts['failed_requests']} |")
    lines += ["", "请求异常包括 HTTP 错误、传输错误和服务端失败；Agent 自己重试成功后，这道题仍可能通过评分。", "",
              "| 题目 | 难度 | Aster | pi | Codex |", "|---|---|---|---|---|"]
    by_job = {(r["agent"], r["instance_id"]): r for r in rows}
    for instance, task in tasks.items():
        statuses = []
        for agent in AGENTS:
            row = by_job.get((agent, instance))
            statuses.append("待执行" if row is None else ("待评分" if not row["graded"] else ("通过" if row["resolved"] else "未通过")))
        lines.append(f"| {instance} | {task['aster_difficulty']} | " + " | ".join(statuses) + " |")
    lines += ["", "解决率以固定十题为分母。服务失败、空补丁和超时均保留；未知用量不当作零费用。耗时为最多两题并行条件下的每题总和，包含服务等待，不代表独占机器速度。", "",
              "评分结果来自 `results/<agent>/official-summary.json`。25 个非空补丁均完成官方评分，评分报告中的基础设施失败和执行错误均为 0；5 个空补丁直接计为未解决。这里的评分成功表示测试成功执行，不表示题目修复成功。", "",
              "代码层面的失败也确实存在：django-15503 中，三者都通过了目标修复测试，却破坏了 6 条已有测试；Aster 在 xarray-3993 中将 dim 改名为 coord，但遗漏了旧参数兼容，官方测试报 unexpected keyword argument 'dim'。不能把所有未通过都归因于环境。", "",
              "模型连接中断使 Aster 的 sympy-13031、pi 的 sympy-18199 和 Codex 的 django-15382 结束时没有补丁；其中 Codex 的 django-15382 未收到任何成功模型响应。另有请求异常后继续运行的任务，见逐题用量记录。所有原始尝试保留，因此本轮不能用于稳定的能力排名。", "",
              "运行期间出现宿主机卡顿：Aster 的 sphinx-9658 与 pi 的 django-15957 同时段模型流中断，均未留下补丁；Aster 的超时终止延迟至约 1041 秒。现场观察到高负载、内存压力历史值和 I/O 等待，但未确认根因。这两次结果保留在分母，不能将其直接归因为 Agent 解题能力；证据见 [host-incident.json](host-incident.json)。", "",
              "这是官方评分器下的固定子集比较，不是完整榜单成绩。题目已在此前 Aster 评测中使用；本轮冻结实现、不根据题目修复产品，仍不能称为全新保留集。", "",
              "协议见 [PROTOCOL.md](PROTOCOL.md)，逐题结果与官方报告在 `results/`；标准预测文件为 `*.predictions.jsonl`。", ""]
    (HERE / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    (HERE / "REPORT.md").write_text("\n".join(lines))
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
