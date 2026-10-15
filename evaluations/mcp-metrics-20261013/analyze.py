"""Read frozen outcomes and calculate paired metrics; no model or network requests."""
from __future__ import annotations

import argparse
import json
import math
import statistics
import tempfile
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import evaluate as experiment

HERE = Path(__file__).resolve().parent


def percentile(values, fraction):
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    low = math.floor(position)
    high = math.ceil(position)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def schema_scaling():
    """Include the extra names in tool_load's enum when enlarging the catalog."""
    catalog = experiment.read(HERE / "catalog.json")
    expanded = dict(catalog)
    for original, (schema, description) in experiment.read(HERE / "full-github-catalog.json").items():
        expanded.setdefault("mcp__github__" + original, {
            "server": "github", "original": original, "schema": schema,
            "description": "[github] " + description, "effect": "read"})
    results = []
    for size in sorted({len(catalog), min(30, len(expanded)), len(expanded)}):
        config = experiment.settings()
        config.mcp_servers["github"].enabled_tools = None
        config.mcp_servers["github"].read_only_tools = list(experiment.read(HERE / "full-github-catalog.json"))
        runtime = experiment.Runtime(experiment.PRIVATE / f"schema-size-{size}", config)
        runtime.start(experiment.TaskSpec(prompt="Measure schema context"))
        subset = dict(list(expanded.items())[:size])
        experiment.populate(runtime, subset)
        available = [n for n in runtime.mcp.schemas if not runtime._unavailable_reason(n)]
        base = runtime._tool_names()
        chosen = ["mcp__filesystem__read_file", "mcp__filesystem__write_file"]
        full = experiment.definitions(base + list(subset), available=available, schemas=runtime.mcp.schemas)
        lazy = experiment.definitions(base + chosen, available=available, schemas=runtime.mcp.schemas)
        enum = next(t for t in lazy if t["name"] == "tool_load")["parameters"]["properties"]["names"]["items"]["enum"]
        assert set(subset) - set(chosen) <= set(enum)
        eager_tokens, lazy_tokens = experiment.COUNTER.count(full), experiment.COUNTER.count(lazy)
        results.append({"catalog_tools": size, "eager_schema_tokens": eager_tokens,
                        "two_loaded_schema_tokens": lazy_tokens, "reduction": 1 - lazy_tokens / eager_tokens,
                        "tokenizer": "cl100k_base", "kind": "offline_schema_only"})
        runtime.sandbox.close()
    experiment.save(HERE / "schema-scaling-corrected.json", results)
    return results


def audit(results, cases):
    checks = Counter()
    for row in results:
        trace = next((experiment.PRIVATE / "runs" / row["id"]).glob(".aster/runs/*/trace.jsonl"))
        assert experiment.digest(trace) == row["trace_sha256"]
        events = [json.loads(line) for line in trace.read_text().splitlines()]
        attempted = [c["call_id"] for e in events if e["event"] == "model_reply" for c in e["calls"]]
        recorded = [e["call"]["call_id"] for e in events if e["event"] == "tool_result"]
        assert attempted == recorded, (row["id"], "tool ledger gap")
        checks["matching_trace_and_tool_ledger"] += 1
    for case in cases:
        with tempfile.TemporaryDirectory(prefix="aster-mcp-score-") as folder:
            root = Path(folder)
            for name, content in case.get("files", {}).items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content)
            if case.get("product"):
                path = root / case["product"]
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(case["gold"]))
            if case.get("deleted"):
                (root / case["deleted"]).unlink()
            state = SimpleNamespace(final=json.dumps(case["gold"]), phase="completed")
            events = [{"event": "tool_result", "call": {"name": name}, "outcome": {"status": "success"}}
                      for name in case.get("required_tools", case["tools"])]
            assert all(experiment.grade(case, state, root, events).values())
            checks["accepted_gold"] += 1
            assert not all(experiment.grade(case, state, root, []).values())
            checks["rejected_missing_mcp_evidence"] += 1
            state.final = "{}"
            assert not all(experiment.grade(case, state, root, events).values())
            checks["rejected_wrong_answer"] += 1
            state.final = json.dumps(case["gold"])
            if case.get("product") or case.get("deleted"):
                path = root / (case.get("product") or case["deleted"])
                path.write_text("{}")
                assert not all(experiment.grade(case, state, root, events).values())
                checks["rejected_wrong_file_state"] += 1
    experiment.save(HERE / "audit.json", dict(checks))


def report(summary, pairs):
    eager, lazy = (summary["arms"][arm] for arm in ("eager", "lazy"))
    change = summary["relative_change_lazy_vs_eager"]
    outside = summary["successful_calls_outside_constraints"]
    extra_lists = sum(t["call"]["name"].endswith("list_directory") for t in outside)
    observed_models = sum(eager["response_models"].get(name, 0) + lazy["response_models"].get(name, 0)
                          for name in {**eager["response_models"], **lazy["response_models"]} if name)
    rows = [
        "# MCP 指标实测（2026-10-13）", "",
        f"计划 8 类任务 × 2 次重复 × 2 组，共 32 次；实际记录 {summary['runs']} 次真实模型运行。模型为 `gpt-5.6-luna`，"
        "使用现有 Aster Runtime、GitHub 官方只读 MCP 和自编文件 MCP。未测试备用服务器。", "",
        "## 全量加载与按需加载", "",
        "eager 在开始前发现并加载当前全部 14 个 MCP 工具，lazy 由模型自行发现和加载。"
        "两组提示、任务、预算、工具权限和内置工具相同；计入 eager 的目录发现耗时。", "",
        "| 指标 | 全量加载 | 按需加载 |", "|---|---:|---:|",
        f"| 独立验收成功 | {eager['passed']}/{eager['runs']} | {lazy['passed']}/{lazy['runs']} |",
        f"| 平均整任务已报告 token | {eager['mean_total_tokens']:,.0f} | {lazy['mean_total_tokens']:,.0f} |",
        f"| 平均模型请求数 | {eager['model_requests']/eager['runs']:.2f} | {lazy['model_requests']/lazy['runs']:.2f} |",
        f"| 平均端到端耗时 | {eager['mean_seconds']:.1f} 秒 | {lazy['mean_seconds']:.1f} 秒 |",
        f"| P50 / P95 耗时 | {eager['p50_seconds']:.1f} / {eager['p95_seconds']:.1f} 秒 | {lazy['p50_seconds']:.1f} / {lazy['p95_seconds']:.1f} 秒 |",
        f"| 首轮工具 Schema 估算 token | {eager['mean_first_schema_tokens']:,.0f} | {lazy['mean_first_schema_tokens']:,.0f} |",
        f"| 首轮 API 已报告 input token（有用量样本数） | {eager['mean_first_input_tokens']:,.0f}（{eager['first_requests_with_usage']}） | {lazy['mean_first_input_tokens']:,.0f}（{lazy['first_requests_with_usage']}） |",
        f"| MCP 业务返回成功 | {eager['mcp_successes']}/{eager['mcp_calls']} | {lazy['mcp_successes']}/{lazy['mcp_calls']} |",
        f"| 符合任务工具及参数约束的成功调用 | {eager['mcp_relevant_valid_calls']}/{eager['mcp_calls']} | {lazy['mcp_relevant_valid_calls']}/{lazy['mcp_calls']} |",
        f"| 客户端 Schema 校验拒绝 | {eager['mcp_invalid_arguments']} | {lazy['mcp_invalid_arguments']} |",
        f"| 服务端参数范围错误 | {eager['mcp_server_parameter_errors']} | {lazy['mcp_server_parameter_errors']} |",
        f"| 平均模型请求累计耗时 | {eager['mean_model_seconds']:.1f} 秒 | {lazy['mean_model_seconds']:.1f} 秒 |",
        f"| 平均 MCP 业务调用累计耗时 | {eager['mean_mcp_seconds']:.2f} 秒 | {lazy['mean_mcp_seconds']:.2f} 秒 |",
        f"| 单次 MCP 调用 P50 / P95 | {eager['p50_mcp_seconds']:.2f} / {eager['p95_mcp_seconds']:.2f} 秒 | {lazy['p50_mcp_seconds']:.2f} / {lazy['p95_mcp_seconds']:.2f} 秒 |", "",
        f"按需加载相对全量加载：首轮 Schema 体积变化 **{change['mean_first_schema_tokens']:+.1%}**，"
        f"整任务已报告 token 变化 **{change['mean_total_tokens']:+.1%}**，平均耗时变化 **{change['mean_seconds']:+.1%}**。"
        "工具定义的缩减与整任务成本必须分别观察：发现、加载产生额外模型轮次，每轮仍需携带系统指令、任务和已有历史。", "",
        "Schema token 包含两组共同保留的内置工具。MCP 业务调用耗时不包含目录发现及工具加载。"
        f"“符合约束”采用冻结脚本的严格规则；{len(outside)} 次成功调用未被它计入，其中 {extra_lists} 次是额外查看工作目录，"
        f"{len(outside)-extra_lists} 次使用 GitHub 接受的提交 SHA 前缀。这些不能等同于选错工具，所有明细保留在 `results/`。", "",
        f"实际有 {eager['mcp_server_parameter_errors']+lazy['mcp_server_parameter_errors']} 次服务端参数范围错误：`read_file(limit=10000)` 超过 2000 上限，"
        "返回 `mcp_tool_error`；模型改为 2000 后成功。该限制没有体现在工具 Schema 的数值边界里，"
        "因此不能用客户端 `invalid_arguments=0` 宣称没有参数错误。", "",
        "## 任务明细", "",
        "每行汇总同一任务的 2 次重复，token 和耗时为算术平均，所有失败也计入。", "",
        "| 任务 | 成功数：全量/按需 | 平均 token：全量/按需 | 平均秒数：全量/按需 |",
        "|---|---:|---:|---:|",
    ]
    for case in experiment.read(HERE / "cases.json"):
        group = [p for p in pairs if p["case"] == case["id"]]
        if not group:
            continue
        a, b = (sum(p[f"{arm}_passed"] for p in group) for arm in ("eager", "lazy"))
        at, bt = (statistics.mean(p[f"{arm}_tokens"] for p in group) for arm in ("eager", "lazy"))
        ad, bd = (statistics.mean(p[f"{arm}_seconds"] for p in group) for arm in ("eager", "lazy"))
        rows.append(f"| {case['id']} | {a}/{len(group)}；{b}/{len(group)} | {at:,.0f} / {bt:,.0f} | {ad:.1f} / {bd:.1f} |")
    rows += ["", "## 检索质量", "",
             "冻结目录上的 14 个工具意图，每种查询形式各 14 条；每题一个相关工具，因此 Recall@K 等于 Hit@K。", "",
             "| 查询 | Recall@1 | Recall@3 | Recall@5 | MRR |", "|---|---:|---:|---:|---:|"]
    for language, label in (("en", "英文自然表达"), ("zh", "中文自然表达"), ("exact", "工具原名正向对照")):
        r = summary["retrieval"][language]
        rows.append(f"| {label} | {r['recall_at_1']:.1%} | {r['recall_at_3']:.1%} | {r['recall_at_5']:.1%} | {r['reciprocal_rank']:.3f} |")
    rows += ["", "当前检索直接匹配名称和描述中的词，中文查询与英文目录没有共同词。"
             "中文任务能否完成还取决于模型是否使用英文查询或空查询枚举；上述 0% 只衡量检索模块，不能等同于中文任务成功率。",
             "模型实际发现查询及次数：`" + json.dumps(summary["lazy_discovery_queries"], ensure_ascii=False) + "`。", "",
             "## Schema 随目录规模的变化", "",
             "仅离线计算工具定义，使用 `cl100k_base`，包含候选名称枚举。30/34 工具使用官方只读端点的真实 Schema 扩展，未运行该规模的模型任务。", "",
             "| 可选 MCP 工具数 | 全量 Schema token | 只加载文件读/写两个工具 | 缩减 |", "|---|---:|---:|---:|"]
    for r in summary["schema_scaling"]:
        rows.append(f"| {r['catalog_tools']} | {r['eager_schema_tokens']:,} | {r['two_loaded_schema_tokens']:,} | {r['reduction']:.1%} |")
    i = summary["idempotency"]
    rows += ["", "## 幂等", "",
             f"真实 filesystem MCP 的 {i['operations']} 次独立写入，追加 {i['replays']} 次重复请求（同会话及恢复后各 10 次）："
             f"**{i['successful_replays']}/{i['replays']} 返回已保存结果**，{i['unchanged_files']}/10 个文件的内容和修改时间保持不变；"
             f"**{i['conflicts_blocked']}/10 次参数冲突被阻止**。本次未观测到重复文件副作用，不能外推为分布式 exactly-once 保证。", "",
             "## 用量与延迟的证据边界", "",
             f"两组共记录 {eager['http_attempts']+lazy['http_attempts']} 次 HTTP 尝试，其中 "
             f"{eager['http_attempts_without_usage']+lazy['http_attempts_without_usage']} 次未返回 usage。"
             "主表保留重试耗时；token 仅汇总 API 返回的数字，不能视作完整账单。"
             "其中 `files_create--eager--1` 完全没有用量记录，对已报告 token 求和贡献为 0，真实消耗未知；"
             "首轮 input token 均值只使用有用量记录。", "",
             f"共 {observed_models} 次成功 HTTP 尝试均报告响应模型为 `gpt-5.6-luna`；其余没有返回模型名。"
             "中途曾因完全无用量失败暂停，经单次接口检查恢复后，仅补跑未开始的任务；没有重跑失败项，"
             "检查请求的 4,396 token 独立记录，不计入主表。详见 [PROTOCOL.md](PROTOCOL.md)。", "",
             "| 用量字段（整组累计） | 全量加载 | 按需加载 |", "|---|---:|---:|"]
    for key in ("input_tokens", "output_tokens", "cache_read_input_tokens", "total_tokens"):
        rows.append(f"| {key} | {eager['usage'].get(key, 0):,} | {lazy['usage'].get(key, 0):,} |")
    clean = summary["transport_clean_pairs"]
    if clean["pairs"]:
        rows += ["", f"另有 **{clean['pairs']} 对**任务的两组全部 HTTP 尝试均成功且返回用量。"
                 f"该辅助子集平均 token 为 {clean['eager_tokens']:,.0f} / {clean['lazy_tokens']:,.0f}，"
                 f"平均耗时为 {clean['eager_seconds']:.1f} / {clean['lazy_seconds']:.1f} 秒（全量/按需）。"
                 f"筛选只取决于传输和用量完整性，主结果仍包含全部 {summary['runs']} 次已记录运行。"]
    rows += ["", "缓存命中受组别顺序、工具定义变化及服务端缓存状态影响；两组虽交错运行，但没有控制服务端缓存。"
             "并发两对任务的公网耗时包含排队和网络波动，不能把全部差异归因于 MCP 本身。"
             "每组至多 16 次短任务，100% 样本成功也不代表生产可靠率。", "",
             "## 复核与复现", "",
             "`audit.json` 核对全部模型工具调用与执行结果一一对应、轨迹哈希、Runtime 与请求用量账本一致；"
             "验收器对已知正确答案放行，对空工具证据、错误答案及错误文件状态拒绝。"
             "Ruff 和已有 3 项操作幂等回归通过。运行方法、事前约定及静态统计修正见 [PROTOCOL.md](PROTOCOL.md)。", "",
             "数据入口：[summary.json](summary.json)、[pairs.json](pairs.json)、[retrieval.json](retrieval.json)、"
             "[idempotency.json](idempotency.json)、[freeze.json](freeze.json)、[results](results/)。"
             "原始请求与会话保存在 `artifacts/private/mcp-metrics-20261013`，未包含于 Git 跟踪文件。", ""]
    if summary["failures"]:
        rows += ["## 保留的失败", "",
                 "每次限 240 秒、单次模型 HTTP 超时 60 秒，沿用 ModelClient 最多 3 次内部尝试。"
                 "取到工具数据但未交付最终答案仍算失败。", "",
                 "| 运行 | 停止原因 | 必需 MCP 调用已成功 | 错误 |", "|---|---|---|---|"]
        for failure in summary["failures"]:
            rows.append(f"| {failure['id']} | {failure['stop_reason']} | {failure['checks']['required_mcp_tools']} | {failure['error']} |")
        rows.append("")
    if summary["missing_runs"]:
        rows += ["## 未完成的计划运行", "",
                 "以下运行尚未执行，不计为成功或失败；本报告只统计已保存的运行。", "",
                 "、".join(f"`{name}`" for name in summary["missing_runs"]), ""]
    (HERE / "REPORT.md").write_text("\n".join(rows))


def summarize(*, allow_partial=False):
    experiment.check_freeze()
    cases = experiment.read(HERE / "cases.json")
    results = [experiment.read(p) for p in sorted((HERE / "results").glob("*.json"))]
    expected = {f"{case['id']}--{arm}--{repeat}" for case in cases
                for arm in ("eager", "lazy") for repeat in (1, 2)}
    missing = sorted(expected - {r["id"] for r in results})
    assert not missing or allow_partial, missing
    assert {r["id"] for r in results} <= expected
    assert len({(r["case"], r["arm"], r["repeat"]) for r in results}) == len(results)
    assert all(r["state_usage_matches"] and r["schema_unchanged"] for r in results)
    assert all(r["freeze_sha256"] == experiment.digest(HERE / "freeze.json") for r in results)
    audit(results, cases)
    tool_failures = []
    for row in results:
        if not any(t["status"] != "success" and t["call"]["name"].startswith("mcp__") for t in row["tools"]):
            continue
        trace = next((experiment.PRIVATE / "runs" / row["id"]).glob(".aster/runs/*/trace.jsonl"))
        for event in (json.loads(line) for line in trace.read_text().splitlines()):
            if (event["event"] == "tool_result" and event["call"]["name"].startswith("mcp__")
                    and event["outcome"]["status"] != "success"):
                outcome = event["outcome"]
                tool_failures.append({"id": row["id"], "arm": row["arm"], "call": event["call"],
                                      "reason": outcome["reason"], "content": outcome["content"],
                                      "server_parameter_error": outcome["reason"] == "mcp_tool_error"
                                      and "limit must be 1..2000" in outcome["content"]})
    queries = experiment.read(HERE / "retrieval.json")
    retrieval = {}
    for language in ("en", "zh", "exact"):
        group = [r for r in queries if r["language"] == language]
        retrieval[language] = {"queries": len(group), **{
            key: statistics.mean(r[key] for r in group)
            for key in ("recall_at_1", "recall_at_3", "recall_at_5", "reciprocal_rank")}}
    arms = {}
    for arm in ("eager", "lazy"):
        group = [r for r in results if r["arm"] == arm]
        usage = dict(sum((Counter(r["usage"]) for r in group), Counter()))
        assert usage["input_tokens"] + usage["output_tokens"] == usage["total_tokens"]
        requests = [q for r in group for q in r["requests"]]
        attempts = [a for q in requests for a in q["attempts"]]
        tools = [t for r in group for t in r["tools"]]
        mcp_seconds = [t["seconds"] for t in tools if t["call"]["name"].startswith("mcp__")]
        first_input = [r["requests"][0]["usage"]["input_tokens"] for r in group
                       if r["requests"] and "input_tokens" in r["requests"][0]["usage"]]
        arms[arm] = {
            "runs": len(group), "passed": sum(r["passed"] for r in group), "usage": usage,
            "mean_total_tokens": usage["total_tokens"] / len(group),
            "noncached_input_tokens": usage["input_tokens"] - usage.get("cache_read_input_tokens", 0),
            "mean_seconds": statistics.mean(r["seconds"] for r in group),
            "p50_seconds": percentile([r["seconds"] for r in group], .5),
            "p95_seconds": percentile([r["seconds"] for r in group], .95),
            **{k: sum(r[k] for r in group) for k in ("model_requests", "mcp_calls", "mcp_successes", "mcp_relevant_valid_calls", "mcp_invalid_arguments", "requests_without_usage")},
            "http_attempts": len(attempts), "http_attempt_classes": dict(Counter(a["classification"] for a in attempts)),
            "http_attempts_without_usage": sum(not a.get("usage_observed") for a in attempts),
            "response_models": dict(Counter(a["response_model"] for a in attempts)),
            "provider_flags": dict(Counter(flag for a in attempts for flag in a.get("flags", []))),
            "mean_first_schema_tokens": statistics.mean(r["requests"][0]["schema_tokens_estimated"] for r in group),
            "mean_first_input_tokens": statistics.mean(first_input),
            "first_requests_with_usage": len(first_input),
            "discovery_calls": sum(t["call"]["name"] == "mcp_list" for t in tools),
            "load_calls": sum(t["call"]["name"] == "tool_load" for t in tools),
            "total_estimated_schema_tokens": sum(q["schema_tokens_estimated"] for q in requests),
            "mean_model_seconds": sum(q["seconds"] for q in requests) / len(group),
            "mean_mcp_seconds": sum(mcp_seconds) / len(group),
            "p50_mcp_seconds": percentile(mcp_seconds, .5),
            "p95_mcp_seconds": percentile(mcp_seconds, .95),
            "mcp_server_parameter_errors": sum(t["server_parameter_error"] for t in tool_failures if t["arm"] == arm),
        }
    pairs = []
    for case in cases:
        for repeat in (1, 2):
            pair = {r["arm"]: r for r in results if r["case"] == case["id"] and r["repeat"] == repeat}
            if len(pair) != 2:
                continue
            eager, lazy = pair["eager"], pair["lazy"]
            complete = all(a.get("usage_observed") and a["classification"] == "success"
                           for r in (eager, lazy) for q in r["requests"] for a in q["attempts"])
            pairs.append({"case": case["id"], "kind": case["kind"], "repeat": repeat,
                          "transport_clean": complete,
                          "eager_passed": eager["passed"], "lazy_passed": lazy["passed"],
                          "eager_tokens": eager["usage"].get("total_tokens", 0), "lazy_tokens": lazy["usage"].get("total_tokens", 0),
                          "eager_seconds": eager["seconds"], "lazy_seconds": lazy["seconds"],
                          "eager_requests": eager["model_requests"], "lazy_requests": lazy["model_requests"]})
    idempotency = experiment.read(HERE / "idempotency.json")
    clean = [p for p in pairs if p["transport_clean"]]
    summary = {"runs": len(results), "planned_runs": len(expected), "missing_runs": missing,
               "retrieval": retrieval, "arms": arms, "tool_failures": tool_failures,
               "schema_scaling": schema_scaling(),
               "idempotency": {"operations": len(idempotency), "replays": len(idempotency) * 2,
                               "successful_replays": sum(r["same_session_replay"] + r["restored_replay"] for r in idempotency),
                               "conflicts_blocked": sum(r["conflict_blocked"] for r in idempotency),
                               "unchanged_files": sum(r["file_unchanged"] for r in idempotency)},
               "relative_change_lazy_vs_eager": {
                   key: arms["lazy"][key] / arms["eager"][key] - 1
                   for key in ("mean_total_tokens", "mean_seconds", "model_requests", "mean_first_schema_tokens", "mean_first_input_tokens", "noncached_input_tokens")}}
    cases_by_id = {case["id"]: case for case in cases}
    summary["successful_calls_outside_constraints"] = [
        {"id": row["id"], "call": tool["call"]} for row in results for tool in row["tools"]
        if tool["status"] == "success" and tool["call"]["name"].startswith("mcp__")
        and not experiment.call_relevant(cases_by_id[row["case"]], tool["call"])]
    summary["transport_clean_pairs"] = {"pairs": len(clean), **{
        f"{arm}_{metric}": statistics.mean(p[f"{arm}_{metric}"] for p in clean)
        for arm in ("eager", "lazy") for metric in ("tokens", "seconds", "requests")}} if clean else {"pairs": 0}
    summary["task_types"] = {
        kind: {arm: {"runs": len(group), "passed": sum(r["passed"] for r in group),
                     "mean_tokens": statistics.mean(r["usage"].get("total_tokens", 0) for r in group),
                     "mean_seconds": statistics.mean(r["seconds"] for r in group)}
               for arm in ("eager", "lazy")
               if (group := [r for r in results if r["kind"] == kind and r["arm"] == arm])}
        for kind in ("github", "files")}
    summary["lazy_discovery_queries"] = dict(Counter(
        t["call"]["arguments"].get("query", "") for r in results if r["arm"] == "lazy"
        for t in r["tools"] if t["call"]["name"] == "mcp_list"))
    summary["failures"] = [{k: r[k] for k in ("id", "checks", "phase", "stop_reason", "error", "seconds")}
                           for r in results if not r["passed"]]
    experiment.save(HERE / "pairs.json", pairs)
    experiment.save(HERE / "summary.json", summary)
    report(summary, pairs)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--allow-partial", action="store_true")
    summarize(allow_partial=parser.parse_args().allow_partial)
