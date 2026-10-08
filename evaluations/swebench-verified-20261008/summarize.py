"""Summarize completed attempts without calling a model or hiding unfinished tasks."""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main():
    manifest = json.loads((HERE / "manifest.json").read_text())
    tasks = manifest["instances"]
    rows = [json.loads(p.read_text()) for p in sorted((HERE / "results").glob("*.json"))]
    by_id = {row["instance_id"]: row for row in rows}
    assert len(by_id) == len(rows) and set(by_id) <= {t["instance_id"] for t in tasks}
    summary = {
        "model": "gpt-5.6-luna", "source_commit": manifest["source_commit"],
        "planned": len(tasks), "completed": len(rows), "resolved": sum(r["resolved"] for r in rows),
        "complete": len(rows) == len(tasks), "difficulty": {},
        "known_total_tokens": sum(r["usage"].get("total_tokens", 0) for r in rows),
        "known_input_tokens": sum(r["usage"].get("input_tokens", 0) for r in rows),
        "known_output_tokens": sum(r["usage"].get("output_tokens", 0) for r in rows),
        "known_cache_read_tokens": sum(r["usage"].get("cache_read_input_tokens", 0) for r in rows),
        "provider_attempts": sum(r["provider_attempts"] for r in rows),
        "attempts_with_total_usage": sum(r["attempts_with_total_usage"] for r in rows),
        "task_seconds_sum": sum(r["seconds"] for r in rows),
        "finished_normally": sum(r["phase"] == "completed" for r in rows),
    }
    private = HERE.parents[1] / "artifacts/private" / HERE.name
    launches = [json.loads(p.read_text()) for p in (private / "runs").glob("*/launch.json")]
    if launches:
        models = {r["settings"]["model"] for r in launches}
        assert len(models) == 1
        summary["model"] = models.pop()
    predictions = [{"instance_id": row["instance_id"], "model_name_or_path": "Aster-" + manifest["source_commit"][:7] + "-" + summary["model"],
                    "model_patch": (HERE / "predictions" / (row["instance_id"] + ".patch")).read_text()} for row in rows]
    (HERE / "predictions.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in predictions))
    lines = ["# SWE-bench Verified 十题本地子集", "", "2026-10-08。单次尝试；原始 F2P/P2P 测试，本地隔离环境，非官方 Docker 榜单成绩。", "",
             f"Aster 源码 `{manifest['source_commit'][:7]}`，模型 `{summary['model']}`。", "",
             f"已完成 **{len(rows)}/10** 题，已通过 **{summary['resolved']}** 题。", "",
             "| 难度 | 计划题数 | 已运行 | 已通过 |", "|---|---:|---:|---:|"]
    for level, label in (("simple", "简单"), ("medium", "中等"), ("hard", "困难")):
        group = [r for r in rows if r["difficulty"] == level]
        counts = {"planned": sum(t["aster_difficulty"] == level for t in tasks), "completed": len(group),
                  "resolved": sum(r["resolved"] for r in group)}
        summary["difficulty"][level] = counts
        lines.append(f"| {label} | {counts['planned']} | {counts['completed']} | {counts['resolved']} |")
    lines += ["", "目标测试（F2P）和原有回归测试（P2P）必须全部通过，才算解决一题。Aster 的 `verified` 只表示它自己的交付检查通过，不等于独立评分通过。", "",
              "| 实例 | 难度 | 独立评分 | F2P | P2P | Aster 终态 | 已报告 token | 秒 |", "|---|---|---|---:|---:|---|---:|---:|"]
    for task in tasks:
        row = by_id.get(task["instance_id"])
        if row:
            status = "通过" if row["resolved"] else row["grade"]["status"]
            counts = [f"{sum(v == 'PASSED' for v in row['grade'][key].values())}/{len(row['grade'][key])}"
                      for key in ("fail_to_pass", "pass_to_pass")]
            lines.append(f"| {task['instance_id']} | {task['aster_difficulty']} | {status} | {counts[0]} | {counts[1]} | {row['phase']} / {row['stop_reason']} | {row['usage'].get('total_tokens', '未知')} | {row['seconds']:.1f} |")
        else:
            lines.append(f"| {task['instance_id']} | {task['aster_difficulty']} | 待完成 | — | — | — | — | — |")
    if summary["complete"]:
        summary["resolved_rate"] = summary["resolved"] / len(tasks)
        lines += ["", f"最终 resolved rate：**{summary['resolved']}/10 = {summary['resolved_rate']:.0%}**。"]
    failures = [r for r in rows if not r["resolved"]]
    if failures:
        observations = {
            "django__django-15503": "修好了数字 JSON 键的目标问题，但让六项原有查询测试失败，属于修复引入回归。",
            "sympy__sympy-18199": "修好了题面给出的零根示例，但原始目标测试还要求合数模数支持；补丁在该场景仍抛出 NotImplementedError。",
            "pydata__xarray-3993": "完成 dim → coord 参数改名，但直接移除了旧参数；原始测试要求旧 dim 参数继续可用并发出 FutureWarning，兼容路径缺失。",
            "pydata__xarray-6992": "修改 DataVariables.__len__ 后，题面中的报错消失；但 reset_index 的坐标、维度及索引状态仍不正确，十二项目标测试失败。",
            "sphinx-doc__sphinx-9229": "未完成生产代码修复，达到 token 预算停止条件；原始目标测试仍失败。",
            "sphinx-doc__sphinx-7590": "首个主任务响应以 failed 结束（incomplete_response），工具执行 0 次、补丁为空。服务失败计入十题分母，不能据此判断该题的解题能力。",
        }
        lines += ["", "## 未通过的题", "", "| 实例 | 本轮观察 |", "|---|---|"]
        for row in failures:
            note = observations.get(row["instance_id"], f"结束状态 {row['phase']} / {row['stop_reason']}；原始测试未全部通过。")
            lines.append(f"| {row['instance_id']} | {note} |")
    lines += ["", f"已报告消耗合计 **{summary['known_total_tokens']:,} tokens**；{summary['attempts_with_total_usage']}/{summary['provider_attempts']} 次供应商尝试有 total usage。该合计只包含已知值，未知部分仍保留为未知，不能作为完整账单。", "",
              "每次运行最多 60 次模型调用、90 步工具、1,000,000 token、900 秒前台时间；记忆默认开启，各题独立。至多两题并行，耗时受共享主机和服务负载影响。", "",
              "这十题是按上游专家修复时间分层、受可用运行环境约束的样本。结果用于观察当前 Aster 与该模型的表现，不能直接与完整榜单排名比较，也不能归因于某项记忆机制。", "",
              "选题、评分口径及原始时间标签见 [协议](PROTOCOL.md)、[环境说明](ENVIRONMENT.md)和 [manifest.json](manifest.json)。逐题结果在 `results/`，候选补丁和脱敏轨迹在 `predictions/`；`predictions.jsonl` 使用 SWE-bench 标准预测字段，方便后续在官方容器环境复核。参考解及原始评分材料不在模型工作区。", ""]
    (HERE / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    (HERE / "REPORT.md").write_text("\n".join(lines))
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
