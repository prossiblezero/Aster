"""Export the ten new attempts and compare official grades with historical results."""
from __future__ import annotations

import gzip
import hashlib
import json
import shutil
import tarfile
from pathlib import Path

from rerun import ORIGINAL, PRIVATE, check_sources

HERE = Path(__file__).resolve().parent
BASELINE = HERE.parent / "swebench-retry-20261009/combined"


def read(path):
    return json.loads(path.read_text())


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def runtime_state(root):
    archive = root / "agent-state.tar"
    if archive.exists():
        with tarfile.open(archive) as bundle:
            for member in bundle.getmembers():
                if member.isfile() and member.name.endswith("/aster-result.json"):
                    state = json.load(bundle.extractfile(member))
                    keys = ("phase", "stop_reason", "turn", "step", "final_revisions", "final",
                            "verification", "verification_failures", "patch_review_requested", "feedback", "usage", "memory_usage")
                    return {key: state.get(key) for key in keys}
    return {"phase": None, "stop_reason": None, "state_missing": True}


def counts(rows):
    result = {key: sum(row.get(key, 0) for row in rows) for key in (
        "known_tokens", "known_input_tokens", "known_output_tokens", "known_cached_tokens", "failed_requests")}
    result.update(
        completed=len(rows), graded=sum(row["graded"] for row in rows),
        resolved=sum(row["resolved"] for row in rows),
        seconds=round(sum(row["seconds"] for row in rows), 3),
        timed_out=sum(row["timed_out"] for row in rows),
        requests_without_usage=sum(row.get("requests_without_usage", row["requests"] - row.get("requests_with_usage", 0))
                                   for row in rows),
        difficulty={d: sum(row["resolved"] for row in rows if row["difficulty"] == d)
                    for d in ("simple", "medium", "hard")},
    )
    result["resolved_rate_percent"] = result["resolved"] * 10 if result["graded"] == 10 else None
    return result


def main():
    manifest = check_sources()
    rows, predictions = [], []
    for task in manifest["instances"]:
        instance = task["instance_id"]
        root = PRIVATE / "runs" / ("aster-" + instance)
        if not (root / "result.json").exists():
            continue
        result, launch = read(root / "result.json"), read(root / "launch.json")
        patch = (root / "prediction.patch").read_bytes()
        assert hashlib.sha256(patch).hexdigest() == result["patch_sha256"]
        previous = read(ORIGINAL / "results/aster" / (instance + ".launch.json"))
        for key in ("model", "reasoning_effort", "seconds_limit", "context_tokens", "output_tokens",
                    "prompt", "image_id", "runner_sha256"):
            assert launch[key] == previous[key], (instance, key)
        predictions.append({"instance_id": instance, "model_name_or_path": "aster-gpt-5.6-luna",
                            "model_patch": patch.decode()})
        metadata = {p.stem: read(p) for p in (root / "provider").glob("*.json")
                    if not p.name.endswith(".request.json")}
        for p in (root / "provider").glob("*.request.json"):
            key = p.name.removesuffix(".request.json")
            metadata.setdefault(key, {"request_id": key, "usage": None, "error": "metadata_missing"})
        observations = sorted(metadata.values(), key=lambda r: r.get("started", 0))
        usage = [r["usage"] for r in observations if r.get("usage")]
        grade_path = PRIVATE / "logs/evaluation" / manifest["run_id"] / "aster-gpt-5.6-luna" / instance / "report.json"
        grade = read(grade_path)[instance] if grade_path.exists() else None
        result.update(
            difficulty=task["aster_difficulty"], requests=len(observations), requests_with_usage=len(usage),
            requests_without_usage=len(observations) - len(usage),
            failed_requests=sum(bool(r.get("error")) or r.get("http_status", 200) >= 400
                                or r.get("terminal") == "response.failed" for r in observations),
            known_tokens=sum(r.get("total_tokens", 0) for r in usage),
            known_input_tokens=sum(r.get("input_tokens", 0) for r in usage),
            known_output_tokens=sum(r.get("output_tokens", 0) for r in usage),
            known_cached_tokens=sum(r.get("input_tokens_details", {}).get("cached_tokens", 0) for r in usage),
            graded=grade is not None or not patch,
            resolved=grade["resolved"] if grade else False,
            grading_status="reported" if grade else ("empty_patch" if not patch else "pending"),
            runtime=runtime_state(root),
        )
        target = HERE / "results/aster"
        save(target / (instance + ".json"), result)
        save(target / (instance + ".launch.json"), launch)
        save(target / (instance + ".usage.json"), [dict(r, usage={k: v for k, v in r["usage"].items() if k != "attribution"})
                                                if r.get("usage") else r for r in observations])
        (target / (instance + ".patch")).write_bytes(patch)
        (target / (instance + ".agent.jsonl.gz")).write_bytes(gzip.compress((root / "agent.jsonl").read_bytes(), mtime=0))
        (target / (instance + ".stderr.log.gz")).write_bytes(gzip.compress((root / "stderr.log").read_bytes(), mtime=0))
        if (root / ".aster.tar").exists():
            with tarfile.open(root / ".aster.tar") as bundle:
                events = []
                for member in bundle.getmembers():
                    if member.isfile() and member.name.endswith("/trace.jsonl"):
                        for line in bundle.extractfile(member):
                            event = json.loads(line)
                            if event["event"] in {"completion_revision", "patch_review_requested", "patch_review_skipped", "verifier_result", "run_completed", "run_stopped"} or (
                                event["event"] == "tool_result" and event.get("call", {}).get("name") in {"run_tests", "patch_file", "write_file", "run_shell"}
                            ):
                                events.append(line)
                (target / (instance + ".verification.jsonl.gz")).write_bytes(gzip.compress(b"".join(events), mtime=0))
        if grade:
            shutil.copy2(grade_path, target / (instance + ".official.json"))
            output = grade_path.parent / "test_output.txt"
            if output.exists():
                (target / (instance + ".tests.txt.gz")).write_bytes(gzip.compress(output.read_bytes(), mtime=0))
        rows.append(result)
    (HERE / "aster.predictions.jsonl").write_text("".join(json.dumps(row) + "\n" for row in predictions))
    save(HERE / "aster.results.json", rows)
    aggregate = PRIVATE / "logs/evaluation" / manifest["run_id"] / "results.json"
    if aggregate.exists():
        shutil.copy2(aggregate, HERE / "results/aster/official-summary.json")
    historical = {agent: read(BASELINE / (agent + ".results.json")) for agent in ("aster", "pi", "codex")}
    historical["aster"] = read(HERE.parent / "swebench-verification-20261009/aster.results.json")
    groups = {"aster_retest": counts(rows), **{agent + "_historical": counts(group) for agent, group in historical.items()}}
    summary = {"planned_new_attempts": 10, "source_commit": manifest["source_commit"], "groups": groups,
               "preflight_usage": read(HERE / "preflight.json")["usage"],
               "runtime_completed": sum(r["runtime"].get("phase") == "completed" for r in rows),
               "completed_but_unresolved": sum(r["runtime"].get("phase") == "completed" and r["graded"] and not r["resolved"] for r in rows)}
    labels = {"aster_retest": "Aster 试验版（含复核）", "aster_historical": "Aster 上一版（历史）",
              "pi_historical": "pi（历史，未重跑）", "codex_historical": "Codex（历史，未重跑）"}
    lines = ["# Aster 过载重试、失败留存与补丁复核：SWE-bench 十题重测", "",
             f"2026-10-09。候选版本 `{manifest['source_commit'][:7]}`，原十题各一次新尝试；同模型、题面、镜像和预算，官方 Docker 评分。", "",
             "结果仍为 **2/10**。最终移除没有显示足够收益的自动追加复核，保留故障处理修复；评测后补齐的断流重试只做本地回归。下列结果对应冻结候选，最终交付代码的差异见后文。", "",
             "| 版本 | 已执行 | 已评分 | 通过 | 简单 | 中等 | 困难 |", "|---|---:|---:|---:|---:|---:|---:|"]
    for key, group in groups.items():
        score = f"{group['resolved']}/10（{group['resolved_rate_percent']}%）" if group["graded"] == 10 else "待评分"
        d = group["difficulty"]
        lines.append(f"| {labels[key]} | {group['completed']} | {group['graded']} | {score} | {d['simple']}/3 | {d['medium']}/5 | {d['hard']}/2 |")
    lines += ["", "Aster 基线使用紧邻本轮的 verification 重测结果；pi、Codex 沿用历史故障补测后的合并成绩。本轮未重跑两者，也不合并 Aster 不同轮次的最好结果。", "",
              "| 题目 | 难度 | Aster 上一版 | Aster 试验版 | pi 历史 | Codex 历史 |", "|---|---|---|---|---|---|"]
    by_group = {key: {r["instance_id"]: r for r in group} for key, group in historical.items()}
    by_group["new"] = {r["instance_id"]: r for r in rows}
    for task in manifest["instances"]:
        statuses = []
        for key in ("aster", "new", "pi", "codex"):
            r = by_group[key].get(task["instance_id"])
            statuses.append("待执行" if r is None else ("待评分" if not r["graded"] else ("通过" if r["resolved"] else "未通过")))
        lines.append(f"| {task['instance_id']} | {task['aster_difficulty']} | " + " | ".join(statuses) + " |")
    lines += ["", "| 版本 | 已报告 token | 输入 | 其中缓存 | 输出 | 未知用量请求 | 请求异常 | 超时题数 | 秒合计 |",
              "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for key, r in groups.items():
        lines.append(f"| {labels[key]} | {r['known_tokens']:,} | {r['known_input_tokens']:,} | {r['known_cached_tokens']:,} | {r['known_output_tokens']:,} | {r['requests_without_usage']} | {r['failed_requests']} | {r['timed_out']} | {r['seconds']:.1f} |")
    lines += ["", "用量来自供应商；未知用量不当作零费用，缓存 token 占比不等于请求缓存命中率。没有供应商单价，不换算货币成本。耗时是并发 2 条件下每题时间的合计。历史行只统计被选入合并成绩的十次尝试，原始故障尝试和补测的累计消耗仍在旧报告中。", "",
              f"本轮另有一次独立连通性检查，已报告 {summary['preflight_usage']['total_tokens']:,} token，不计入十题消耗。", "",
              "| 题目 | 用时（秒） | 已报告 token | 退出码 | Agent 最终状态 | 未消除的已知失败 | 官方结果 |",
              "|---|---:|---:|---:|---|---:|---|"]
    for r in rows:
        state = r["runtime"]
        failed = len(state.get("verification_failures") or {}) if not state.get("state_missing") else "未知"
        status = ("通过" if r["resolved"] else "未通过") if r["graded"] else "待评分"
        lines.append(f"| {r['instance_id']} | {r['seconds']:.1f} | {r['known_tokens']:,} | {r['exit_code']} | {state.get('phase') or '未留最终状态'} | {failed} | {status} |")
    lines += ["", "Agent 自报完成、进程退出正常和官方测试通过是不同口径。逐题 JSON 保存验证状态和失败标识，官方报告及测试日志保存在 results/aster/。", ""]
    observations = HERE / "OBSERVATIONS.md"
    if observations.exists():
        lines += [observations.read_text().strip(), ""]
    lines += ["这十题已用于调试，是固定子集回归评测，不是完整榜单或新保留集。每题一次，分数变化不能单独归因于补丁复核或任何一项修复，也不能据此判定稳定排名。", "",
              "[协议](PROTOCOL.md) · [冻结信息](manifest.json) · [历史对比](../swebench-retry-20261009/REPORT.md)。标准预测为 aster.predictions.jsonl，逐题结果为 aster.results.json。", ""]
    save(HERE / "summary.json", summary)
    (HERE / "REPORT.md").write_text("\n".join(lines))
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
