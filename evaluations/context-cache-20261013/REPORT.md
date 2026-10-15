# 上下文缓存 v9 实施记录

2026-10-14 按 [Claude Code 的实施方案](../../docs/context-cache-plan.md)完成 Stage 0–3，并在用户确认后完成 Stage 4：只重跑 Aster old20，独立通过 **13/20**，低于历史基线 **15/20**。已报告总 token **增加 21.6%**，未缓存输入 **减少 35.9%**；**本轮未通过验收**。下方保留离线实施记录，并列出完整线上对比与失败证据。

## 已落地的行为

- Responses 前台请求以 `state.session_id` 作为 `prompt_cache_key`。续接、恢复保持键；新会话和 Worker 使用自己的键。后台记忆使用 `memory-<job_id>`。请求快照、哈希、预算与实际发送路径一致。
- 使用上一次干净成功回执的输入和输出用量，只估算新增工具结果、用户消息及尾部增量。缺失、异常、混合重试用量不建立锚；计量回退保留密文校准和加性供应商开销。作用域随会话、协议、模型变化而重置。
- 历史未压缩时只追加；达到阈值后先将旧工具正文替换为可回读占位符，仍不足才一次丢弃旧组至半窗口并维护累计说明。保留原有小预算兜底、最新交换、用户消息和重要消息约束。
- 一般工具的长结果在写入时归档并预览；`read_file` 限制为 24,000 字符，按实际可见行返回 `end` 和 `next_start`。首次读取的单行超过上限时，只显示该行前 24,000 字符，这是方案明确保留的限制。
- 新增 `estimate_basis`、`prefix_drift`、`first_changed_item`、`masked_outputs` 事件字段；策略版本为 `deterministic-v9`。

生产改动限于 `context/budget.py`、`core/runtime.py`、`providers/codec.py`、`providers/client.py`、`knowledge/maintenance.py`、`tools/workspace.py`、`training/env.py` 七个文件。没有增加依赖、配置项或模型调用；检查点、系统提示词、工具定义、尾部动态消息位置、归档格式和 TeamBudget 公式均保持原样。

## 离线验收

[metrics.py](metrics.py) 对 50 题和 old20 的所有规定基线指标复算一致，包括 Python exclusive quantile 定义的 p10。结果见 [50 题](baseline50.md)、[old20](baseline-old20.md)、[校验记录](baseline-metrics-check.json)。

| Aster 50 题前台指标 | 复算结果 |
|---|---:|
| 请求 / 有回执后续请求 | 825 / 761 |
| 改写请求 | 259 |
| 改写请求的未缓存输入 | 5,408,244 |
| 前缀稳定但未命中 | 2,134,725 |
| 未缓存输入 | 8,576,179 |
| 缓存占比 | 47.98% |

[replay_metering.py](replay_metering.py) 回放生产 `BudgetCalculator`，不调用供应商。明细见 [replay.json](replay.json)。

| 回执估算 / 实际输入 | 结果 | 门槛 |
|---|---:|---:|
| 中位数 | 1.00520 | ≤ 1.02 |
| p95 | 1.02856 | ≤ 1.05 |
| 最小值 | 0.99930 | ≥ 0.99 |
| 最大值 | 1.10666 | ≤ 1.15 |
| 稳定后续请求的回执估算覆盖 | 499 / 502（99.40%） | ≥ 95% |

40 轮确定性测试确认恰好一次前缀改写、每个工具输出至多“原文→占位符”一次、最新交换保留、所有交换配对合法。另验证归档全文回读、重复压缩累计说明、缺锚阈值、长 diff 归档失败处理、文件完整行分页、Chat/Anthropic 原生归档页的回读提示。

## 测试基线与结果

执行环境：`/home/dzh/.conda/envs/aster/bin/python`，包含 `tiktoken`；基于 HEAD `0ce78db` 保存修改前源码及测试。

- 基线：核心 468 通过；其余 788 通过、6 失败、4 跳过。
- Stage 1 缓存键：完成全部回归；两个测试替身同步新参数后，新增失败为 0。
- Stage 2 回执计量：核心 473 通过；其余 805 通过、原有 6 失败、4 跳过。
- Stage 3 最终回归：核心 473 通过；其余 814 通过、原有 6 失败、4 跳过。合计 1287 通过，较基线增加 31 项测试，新增失败为 0。原生协议兼容处理后的相关 104 项回归也通过。
- Ruff 检查生产代码、测试及本次脚本通过。完整日志保存在本目录，最终差异清单见 `validation.json`。

原有失败清单：

```text
tests/test_context_archives.py::test_latest_parallel_batch_fits_one_budget_with_all_results[responses]
tests/test_context_archives.py::test_latest_parallel_batch_fits_one_budget_with_all_results[chat]
tests/test_context_policy_input.py::test_opted_in_worker_fits_small_context[responses-True-False-512]
tests/test_context_policy_input.py::test_opted_in_worker_fits_small_context[responses-True-True-1024]
tests/test_context_policy_input.py::test_opted_in_worker_fits_small_context[chat-True-False-512]
tests/test_context_policy_input.py::test_opted_in_worker_fits_small_context[chat-True-True-1024]
```

这些都是 4096 token 小窗口场景，未计作本次修复或新增回归。

## 与方案的必要兼容调整

1. 当前 Aster 能动态加载工具。锚额外校验模型、工具定义及系统字段的请求形状；加载新工具后回退本地计量，避免用旧工具集合的回执低估当前输入。
2. 后台记忆的真实 `ModelClient` 传递缓存键；与前台一致，非 `ModelClient` 的自定义测试策略保持原有 `complete(messages, tools)` 接口。
3. 普通消息与原生工具记录继续支持三个协议；掩码解析 Chat 的 `tool_calls` 和 Anthropic 的 `tool_use`，保证历史归档页被掩码后仍指向正确的回读工具。
4. 压缩过程的比例修正使用整数向上取整，避免浮点误差把未变化请求多计一个 token。压缩后的计量仍是方案中的近似；归档回放验证的是既有轨迹上的估算，不等价于新策略的线上端到端验证。

## 既有测试调整清单

| 文件 / 断言 | 原断言或接口 | 新断言或接口 | 原因 |
|---|---|---|---|
| `test_stable_prefix.py`、`test_context_policy_input.py` | policy v7 | policy v9 | 策略版本更新 |
| `test_context_provider.py`、`test_context_archives.py`、`test_context_state.py`、`test_model_context_config.py`、`test_stable_prefix.py` | 重算 payload 没有缓存键 | 重算时补同一 `session_id` | 继续验证线上请求与预算的一致性 |
| `test_context_provider.py::test_compaction_retains_full_call_batches_and_reasoning_beyond_old_slice_limit` | 所有历史组都恰好 3 项 | 第一组为明确的压缩说明，其余交换仍恰好 3 项 | 允许方案规定的累计说明；最新推理、调用与结果断言保留 |
| `test_tool_pagination.py::test_search_pages_all_1200_matches_in_one_file` | `result.content` 直接含每页 500 / 500 / 200 条 | 若返回预览，先通过 `observation_read` 分页还原；仍核对 500 / 500 / 200、全部游标以及 1200 条逐项相等 | 写入时限长新增归档引用，原工具说明已要求预览后先回读再翻页 |
| `test_cli_contract.py`、`test_tui_streaming.py` | ModelClient 测试替身没有新关键字 | 接受 `prompt_cache_key=None` | 同步新增方法参数；流式、取消和隐私断言不变 |

## Stage 4：20 题线上结果（2026-10-14）

**未通过验收。** 全部 20 题均已独立评分，Aster v9 为 13/20（65%），旧 Aster 为 15/20（75%）；没有重跑或择优拼接。含后台请求的已报告总 token 增加 21.6%，未缓存输入减少 35.9%。

| Agent | 独立通过 | 正常交付且通过 | 请求数 | 已报告总 token | 未缓存输入 | 累计答题分钟 |
|---|---:|---:|---:|---:|---:|---:|
| Aster v9（本轮） | 13/20 | 11/20 | 394 | 10,186,919 | 2,812,558 | 144.7 |
| Aster（历史基线） | 15/20 | 12/20 | 405 | 8,376,153 | 4,385,656 | 156.3 |
| pi（历史同题） | 14/20 | 13/20 | 240 | 4,936,079 | 803,852 | 66.7 |
| Codex（历史同题） | 15/20 | 15/20 | 254 | 6,212,836 | 701,823 | 73.0 |

本轮只跑 Aster；其余三行来自原 50 题中相同 old20 的冻结结果。候选是 `0ce78db` 加 v9，历史基线是 `c6b651d`，因此差异还包括其间的 MCP/会话改动，不能将全部分数或用量变化单独归因于 v9。这 20 题已用于开发，属于本地改编集合，不能据此更新完整 50 题成绩或宣称官方排名。

| 用量补充（含后台） | Aster v9 | 旧 Aster | pi | Codex |
|---|---:|---:|---:|---:|
| 输入 token | 10,078,350 | 8,256,888 | 4,860,684 | 6,134,143 |
| 缓存输入 token | 7,265,792 | 3,871,232 | 4,056,832 | 5,432,320 |
| 输出 token | 108,569 | 119,265 | 75,395 | 78,693 |
| 缺失用量请求 | 9 | 11 | 2 | 5 |
| 超时任务 | 3 | 4 | 0 | 0 |

请求总数只减少 2.7%，总输入增加 22.1%，输出减少 9.0%。未缓存输入仍为 pi 的 3.50 倍、Codex 的 4.01 倍；不能把未缓存输入的下降直接换算成账单降幅。计数使用已收到的用量，缺失用量不补零、不推算金额。

### 机制验收

| 指标（前台） | 旧 Aster | Aster v9 | 门槛 | 达标 |
|---|---:|---:|---:|:---:|
| 改写 / 有用量后续请求 | 146/368（39.67%） | 2/360（0.56%） | ≤3% | 是 |
| 改写请求的未缓存输入 | 2,787,012 | 49,896 | ≤250,000 | 是 |
| 非预期 prefix_drift | — | 0 | 0 | 是 |
| 所有发送的改写均对应 compacted | — | 是 | 是 | 是 |
| 稳定前缀命中均值 | 0.718 | 0.783 | ≥0.85 | 否 |
| 回执估算/实际，中位数 / p95 | — | 1.00386 / 1.02451 | ≤1.02 / ≤1.05 | 是 |
| 缓存输入占比 | 46.96% | 72.30% | ≥78% | 否 |
| 未缓存输入 | 4,356,424 | 2,782,173 | ≤1,300,000 | 否 |
| 总输入 | 8,213,832 | 10,043,357 | ≤9,450,000 | 否 |
| 独立通过 | 15/20 | 13/20 | ≥15/20 | 否 |

轨迹共记录 383 次构建、3 次显式压缩、48 项输出掩码；382 次前台请求均带缓存键。标准指标按冻结定义只统计有用量回执的改写，因此是 2 次；解释器最后一次压缩请求已发出，但整题超时后断连，没有用量回执。额外核对全部已发送请求后，3 次改写均对应压缩，未发现非预期改写。359 次成功回执估算的最小/最大比值为 0.99901/1.13459。

剩余的主要问题是稳定前缀仍未充分命中：stable_miss 从 1,109,691 增至 2,098,041。54 次稳定前缀请求的命中不足上一次输入的一半，其中 13 次为零命中；这些请求的工具定义、缓存键和保留键顺序的历史前缀均相同。该证据不能确定供应商是否采用了缓存键，也不能单独定位服务端原因。

前缀改写已达标，而稳定命中均值仍低于 0.80，按冻结协议停止继续调整上下文策略；下一步应先定位传输、推理内容重放与供应商缓存行为。本轮没有证明整个降本方案可接受。

### 失败与交付

新增回归只有异步取消、内存堆两题，没有新增通过题。这两题均无压缩、无前台请求异常；不能将回归归为外部供应商故障，也没有证据证明是压缩丢失信息。按协议 13/20 直接不通过。

| 失败题 | 旧版 | 本轮内部状态 | 压缩次数 | 独立测试与轨迹证据 |
|---|:---:|---|---:|---|
| `django__django-11400` | 失败 | 完成 | 0 | 两项 get_choices 默认排序断言失败。内部仅运行 admin_filters，自测未覆盖独立测试中的 model_fields 默认排序。 |
| `sympy__sympy-17630` | 失败 | 完成 | 0 | test_zero_matrix_add 失败：Add(ZeroMatrix(2, 2), ZeroMatrix(2, 2)) 没有返回预期的 ZeroMatrix。 |
| `sphinx-doc__sphinx-11510` | 失败 | 完成 | 1 | include 的 source-read 事件与嵌套 include 两项断言失败。唯一一次压缩发生在最后的最终回复前；该次回复之后没有再修改代码。 |
| `pytest-dev__pytest-10356` | 失败 | 超时 | 0 | 达到 900 秒上限；保存的补丁在 test_mark_mro 失败，get_unpacked_marks 返回 generator，独立测试预期 list。 |
| `terminal-cancel-async-tasks` | 通过 | 完成 | 0 | 3 个任务、并发上限 2 时，SIGINT 后没有完成清理，Cleaned up. 为 0 而预期为 2。自测覆盖了单任务取消，漏掉排队任务与异步清理的组合。 |
| `terminal-custom-memory-heap-crash` | 通过 | 停止 | 0 | Release 构建在计算完成后以 -11（SIGSEGV）退出；Debug 与 Valgrind 检查通过，Release 退出崩溃仍未修复。 |
| `terminal-kv-store-grpc` | 失败 | 完成 | 0 | 评分时 5328 端口没有监听，真实 RPC 调用失败。轨迹 seq=2716 中 python /app/server.py 返回 Permission denied；之后只验证了内存方法，最终仍被内部验收放行。属于隔离路径使用与服务启动验收问题，不能称为纯供应商故障。 |

内部标记完成 16 题，其中 5 题独立测试失败。Sphinx 9230 和解释器产物通过，但均超时未正常交付；pytest 10356 超时且测试失败，因此正常交付且通过为 11/20。所有任务均有独立测试结果，没有评分器基础设施失败。

本轮 12 次后台记忆请求已包含在总计中，5 次有用量（输入 34,993、缓存输入 4,608、输出 1,294），7 次缺用量。异常包括 6 次后台 BrokenPipe 和 1 次后台 HTTP 503；503 发生在 Sphinx 11510，不能据此把前台补丁的两项断言失败算成纯供应商失败。另有 2 次前台 BrokenPipe，分别对应超时的 Sphinx 9230 和解释器，不能仅凭 HTTP 200 后断连断言供应商宕机。

### 执行与复核

整轮墙钟上界约 34.0 分钟，含容器准备与独立评分。累计答题时间为各任务秒数相加，与六槽并发墙钟不同。控制进程正常退出；本轮容器、网络和中继 socket 均已清理。

已核对 20 个唯一尝试、20 份评分，任务提示词、镜像、模型、900 秒上限和底层运行器均与历史同题一致。冻结源码及输入哈希通过，394 个实际请求字节哈希全部通过；前台请求全部关联到保存的 context_built 快照。没有修改题目、测试、冻结协议或已运行源码。

实际运行使用已有的 `artifacts/private/swebench-compare-20261008/controller-env/bin/python` 控制环境，它提供 Docker SDK 与 SWE-bench；Aster 和网关仍使用原项目 Python。冻结协议中的项目 Python 本身没有 Docker SDK，因此执行入口的这项修正记录在 execution.json，未改动冻结脚本或依赖。

原始汇总：[BENCHMARK.md](BENCHMARK.md)、[逐题 results.json](results.json)、[用量汇总](summary.json)、[前台指标](metrics.json)。验收证据：[ACCEPTANCE.md](ACCEPTANCE.md)、[完整审计](audit.json)、[失败记录](failure-notes.json)、[执行记录](execution.json)。

可用以下命令离线重新生成计数和验收表，不调用模型：

```bash
artifacts/private/swebench-compare-20261008/controller-env/bin/python evaluations/context-cache-20261013/evaluate.py results
/home/dzh/.conda/envs/aster/bin/python evaluations/context-cache-20261013/audit.py
```
