# 阶段评测与指标证据

本页汇总 Aster 各阶段的量化结果，提供实验条件、报告和原始数据入口。每项数字归属对应的版本与任务集。归档链接固定到提交 [`d4264c2`](https://github.com/prossiblezero/Aster/tree/d4264c29dc76fd78c02f5e04712585f8f351cc7e)，报告进一步记录被测源码版本、预算与评分方法。

## 任务通过率与成本

2026-10-19，Aster v15-rc3 使用 GPT-5.6 Luna、单 Agent 配置（`max_workers=0`）。在 fresh30b 排除供应商故障及 write-compressor 后的 23 道共同可比题上，独立通过且正常交付 **20/23（87.0%）**；pi 为 **21/23**，Codex 为 **20/23**。pi、Codex 使用 10-18 的历史运行，题集已参与开发。

同批任务的加权估算成本为 **Aster 1.29、pi 1.20、Codex 1.45**，Aster 较 Codex 低约 **11.0%**。成本统一按 `(未缓存输入 × 1.25 + 缓存输入 × 0.125 + 输出 × 10) / 1e6` 计算。Aster 有 21 个请求缺少供应商用量，1.29 按已知请求的平均值补齐；只累计已报告用量时为 1.22。

- [报告 §15.2 与 §15.4][harness-report]：同批比较、缺失用量处理与多模型结果。
- [Aster 逐题结果][harness-aster]、[pi 逐题结果][harness-pi]、[Codex 逐题结果][harness-codex]：完整运行包含被排除的任务，比较时使用报告中的共同样本口径。
- [固定任务集与预检][fresh30b]、[Aster 评测驱动][harness-driver]：题目、环境与预算。

## 跨会话记忆

2026-10-05，十个定向学习后的跨会话任务，每题每版本一次，两组均开启记忆。复用阶段正常交付且独立断言通过由 **4/10 → 7/10（40% → 70%）**；学习和复用两个阶段均完成且通过为 **4/10 → 6/10**。两阶段总 token 增加 **1.7%**。

- [报告][memory-report]：任务条件、成功定义及逐项分析。
- [汇总与逐题断言][memory-data]：`baseline.task_success=4`、`knowledge.task_success=7`，各 `tasks=10`。
- [任务与运行代码][memory-directory]：定向学习、全新 Runtime 复用及独立评分。

## 缓存输入占比

2026-10-14，Aster v10 在 old20 开发集上的已报告输入为 **9,710,057 tokens**，其中缓存输入 **8,545,280 tokens**，占比 **88.0%**。统计包含后台记忆；分子、分母均取供应商已报告用量，衡量缓存 token 占比。

- [成本汇总][cache-report]、[v10 实验报告][cache-detail]：优化前后数据与追加式上下文机制。
- [汇总 JSON][cache-data]：`agents["Aster v10"].cached_tokens / input_tokens`。
- [离线汇总脚本][cache-script]：从冻结结果重新计算表格。

## 上下文压缩

2026-09-16，被测版本 `01638a7`。12 道代码任务使用预置代码、日志和中文历史，选定 **8,192 输入预算**后，每题全新运行三次，**36/36 完成**。首请求平均压缩 **57.8%**，本地估算输入均值由 **18,434.83 → 7,779.25 tokens**。

压缩率先按每次运行计算，再取均值。确认批次使用已参与选参的开发题；实验没有同期未压缩对照，因此结果表述为压缩量与完成数。

- [完整报告][context-report]：预算选择、确认批次及控制题。
- [选择与确认数据][context-selection]、[确认批次逐题报告][context-data]：冻结记录、哈希与全部尝试。
- [预先登记的协议与命令][context-protocol]：候选预算及选择门槛。

## 会话操作数

2026-09-20，六类固定工作流各三个变体，新旧版本各 18 条脚本记录，均完成。调用真实 Runtime、SQLite 和 TUI 命令，模型使用离线替身；按预定义规则统计命令、选择、复制和粘贴次数，总操作数 **69 → 36，减少 47.8%**。

- [报告][session-report]：六类工作流与计数规则。
- [成对数据][session-data]：`baseline_operations=69`、`candidate_operations=36`。
- [旧版记录][session-baseline]、[新版记录][session-candidate]：逐条操作、断言及源码摘要。

## 功能与实验入口

MCP 的当前接入方法、实现和测试见 [MCP 指南](mcp.md)；工程回归与新评测命令见[评测指南](benchmark.md)。历史实验使用过的任务继续作为开发集维护。复核数字时按上述链接中的版本、任务集合和统计范围读取。

[harness-report]: https://github.com/prossiblezero/Aster/blob/d4264c29dc76fd78c02f5e04712585f8f351cc7e/evaluations/harness-v12-20261017/REPORT.md#L389
[harness-aster]: https://github.com/prossiblezero/Aster/blob/d4264c29dc76fd78c02f5e04712585f8f351cc7e/evaluations/harness-v12-20261017/runs/fin-fresh30b-gpt-v15/results.json
[harness-pi]: https://github.com/prossiblezero/Aster/blob/d4264c29dc76fd78c02f5e04712585f8f351cc7e/evaluations/harness-v12-20261017/runs/fresh30b-pi/results.json
[harness-codex]: https://github.com/prossiblezero/Aster/blob/d4264c29dc76fd78c02f5e04712585f8f351cc7e/evaluations/harness-v12-20261017/runs/fresh30b-codex/results.json
[fresh30b]: https://github.com/prossiblezero/Aster/tree/d4264c29dc76fd78c02f5e04712585f8f351cc7e/evaluations/fresh30b-20261018
[harness-driver]: https://github.com/prossiblezero/Aster/blob/d4264c29dc76fd78c02f5e04712585f8f351cc7e/evaluations/harness-v12-20261017/evaluate.py
[memory-report]: https://github.com/prossiblezero/Aster/blob/d4264c29dc76fd78c02f5e04712585f8f351cc7e/evaluations/memory-knowledge-20261005/REPORT.md
[memory-data]: https://github.com/prossiblezero/Aster/blob/d4264c29dc76fd78c02f5e04712585f8f351cc7e/evaluations/memory-knowledge-20261005/summary.json
[memory-directory]: https://github.com/prossiblezero/Aster/tree/d4264c29dc76fd78c02f5e04712585f8f351cc7e/evaluations/memory-knowledge-20261005
[cache-report]: https://github.com/prossiblezero/Aster/blob/d4264c29dc76fd78c02f5e04712585f8f351cc7e/evaluations/cost-summary-20261016.md
[cache-detail]: https://github.com/prossiblezero/Aster/blob/d4264c29dc76fd78c02f5e04712585f8f351cc7e/evaluations/context-state-20261014/REPORT.md
[cache-data]: https://github.com/prossiblezero/Aster/blob/d4264c29dc76fd78c02f5e04712585f8f351cc7e/evaluations/context-state-20261014/summary.json
[cache-script]: https://github.com/prossiblezero/Aster/blob/d4264c29dc76fd78c02f5e04712585f8f351cc7e/evaluations/cost-summary-20261016.py
[context-report]: https://github.com/prossiblezero/Aster/blob/d4264c29dc76fd78c02f5e04712585f8f351cc7e/docs/context-results.md
[context-selection]: https://github.com/prossiblezero/Aster/blob/d4264c29dc76fd78c02f5e04712585f8f351cc7e/artifacts/benchmark-v8-confirmation/budget-selection.json
[context-data]: https://github.com/prossiblezero/Aster/blob/d4264c29dc76fd78c02f5e04712585f8f351cc7e/artifacts/benchmark-v8-confirmation/report.json
[context-protocol]: https://github.com/prossiblezero/Aster/blob/d4264c29dc76fd78c02f5e04712585f8f351cc7e/datasets/context_budget_protocol.md
[session-report]: https://github.com/prossiblezero/Aster/blob/d4264c29dc76fd78c02f5e04712585f8f351cc7e/evaluations/session-ux-20260920/REPORT.md
[session-data]: https://github.com/prossiblezero/Aster/blob/d4264c29dc76fd78c02f5e04712585f8f351cc7e/evaluations/session-ux-20260920/comparison.json
[session-baseline]: https://github.com/prossiblezero/Aster/blob/d4264c29dc76fd78c02f5e04712585f8f351cc7e/evaluations/session-ux-20260920/baseline.json
[session-candidate]: https://github.com/prossiblezero/Aster/blob/d4264c29dc76fd78c02f5e04712585f8f351cc7e/evaluations/session-ux-20260920/candidate.json
