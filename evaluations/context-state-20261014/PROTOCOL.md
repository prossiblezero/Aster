# Context state v10：old20 单次验收协议

制定日期：2026-10-15。来源：[实施方案](../../docs/context-state-plan.md)第 7 节。
状态：仅准备并冻结；尚未调用模型。用户确认后才执行 `run`。

## 固定对象与运行条件

- 选择 `general-coding-50-20261012` 中 `exposure == "old20"` 的全部 20 题，按 `sha256("schedule:" + task_id)` 排序。
- 只重跑 Aster，每题新会话、一次尝试。对照为已保存的 Aster v9 单次结果，以及原 50 题中的旧 Aster、pi、Codex；不重跑对照、不合并最佳成绩。
- 候选是本轮开始时的 v9 工作区加 v10 改动，以私有运行时快照和 SHA-256 冻结。HEAD 不是候选源码的完整标识。旧 Aster 基线还包含此前 MCP/会话版本差异，不能把与旧基线的全部差距归因于 v10。
- 复用原任务输入、容器镜像、依赖、网关、执行器及独立评分器；不修改题目与测试。记录全部失败、超时、缺用量和异常。
- `gpt-5.6-luna`，Responses，medium，64000 上下文，8192 输出上限，900 秒/题，4 CPU / 8 GiB，6 槽并发。原生 sandbox、严格验收、`max_workers=0`、自动记忆开启。
- 沿用网关将后台记忆请求统一为 medium/8192 的行为。前台机制指标排除工具列表恰为 `[memory_changes]` 的后台请求；全部消耗表计入后台。
- 不追加付费 smoke，不补跑失败题，不择优。连续 6 题只有供应商故障时停止提交后续任务，保留未完成状态，不据部分题目推算通过率。
- 使用原 50 题环境预检及本轮离线回归。执行前验证冻结源码、输入、外部二进制哈希；环境有变更时先运行不调用模型的预检。

## 指标定义与门槛

`requests` 包含全部前台请求；token 总量只累加有供应商用量的记录，缺失数单列，不填估值。`followups` 比较同题相邻的有输入用量请求。完整前缀使用规范化 JSON 逐项比较。

保留 v9 的 `rewrites`（允许末项替换）、`stable_hit_mean` 和 `low_hits` 口径，用于直接比较。稳定请求分母为 `followups - rewrites`；命中值为 `min(cached / previous_input_tokens, 1)`，低命中为 `cached < 0.85 * previous_input_tokens`。v10 的正确性另用 `strict_rewrites` 验证完整前缀，且检查全部实际发送请求（包括缺用量）中的改写是否有对应 `compacted` 事件。

| 指标 | v9 离线复算 | v10 要求 |
|---|---:|---:|
| strict_rewrites / followups | 360 / 360 | ≤ 3% |
| 改写对应显式压缩 | — | 所有实际发送的改写均对应 compacted=true |
| prefix_drift 事件 | 0 | 0 |
| stable_hit_mean | 0.783453 | ≥ 0.88 |
| low_hits / 稳定请求 | 120 / 358 | ≤ 15% |
| 回执估算 / 实际，中位数 / p95 | 1.004 / 1.025 | ≤ 1.02 / ≤ 1.05 |
| 前台未缓存输入 | 2,782,173 | ≤ 1,300,000 |
| 前台缓存占比 | 0.722984 | ≥ 0.85 |
| 前台输入 | 10,043,357 | ≤ 10,043,357 |
| 独立通过 | 13 / 20 | ≥ 15 / 20 |

请求与 context_built 快照须全部关联；评分前要求 20 个唯一任务全部完成独立评分。无可用分母或无回执时，对应门槛记为未通过。回执比例只使用 `estimate_basis=receipt` 且成功、无 flags 的独立响应诊断，p95 沿用 Python exclusive 插值分位数。

额外报告前台/总请求数、输出与总 token、缺用量、累计答题时间、正常交付且通过数、逐题成绩变化。平均条目数使用全部前台请求；平均输入 token 使用有输入用量的前台请求。`context_notes` 数量及局部 token 计数按 context_built 事件汇总，不等同于供应商实际收费量。缓存率不能直接换算为金额节省。

直接引用 `cache-probe-20261014/boundary_model.py`，v10 使用 `--append-only`，令可替换尾部长度为 0，并检查完整前缀。默认模式保留 v9 行为。原脚本会把末项当作可替换尾部，工具结果也可能没有 content，故不能直接用原默认模式分析 v10。`low:tail_straddle` 应接近 0；它是诊断信息，不新增成绩门槛。边界模型是供应商观测的近似模型，不是计费依据。

## 复现命令

均在仓库根目录执行。`run` 的控制环境包含 Docker SDK 和 SWE-bench；Agent 和网关仍用项目 Python。

```bash
# 离线复算历史；无模型调用。
/home/dzh/.conda/envs/aster/bin/python evaluations/context-state-20261014/metrics.py artifacts/private/context-cache-20261013/runs --output evaluations/context-state-20261014/baseline-v9.json
/home/dzh/.conda/envs/aster/bin/python evaluations/cache-probe-20261014/boundary_model.py artifacts/private/context-cache-20261013/runs

# 冻结与校验；无模型调用。prepare 只执行一次。
/home/dzh/.conda/envs/aster/bin/python evaluations/context-state-20261014/evaluate.py prepare
/home/dzh/.conda/envs/aster/bin/python evaluations/context-state-20261014/evaluate.py verify

# 用户确认后运行；恰好 20 次任务尝试，保留原生请求重试与后台记忆。
/home/dzh/Aster/artifacts/private/swebench-compare-20261008/controller-env/bin/python evaluations/context-state-20261014/evaluate.py run

# 运行结束后离线统计、审计与边界模型分析。
/home/dzh/.conda/envs/aster/bin/python evaluations/context-state-20261014/evaluate.py results
/home/dzh/.conda/envs/aster/bin/python evaluations/context-state-20261014/audit.py
/home/dzh/.conda/envs/aster/bin/python evaluations/cache-probe-20261014/boundary_model.py artifacts/private/context-state-20261014/runs --append-only > evaluations/context-state-20261014/boundary.json
```

冻结清单包含候选源码、运行时、原任务、历史成绩、协议、方案和执行/统计脚本。已启动但无结果的任务必须先分类，不能重新尝试；只有未启动任务可继续提交。本轮基线备份位于 `artifacts/private/context-state-20261014/baseline/`，源码差异只针对该备份，不覆盖此前未提交改动。
