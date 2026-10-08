# SWE-bench Verified 十题本地子集

2026-10-08。单次尝试；原始 F2P/P2P 测试，本地隔离环境，非官方 Docker 榜单成绩。

Aster 源码 `5cd7200`，模型 `gpt-5.6-luna`。

已完成 **10/10** 题，已通过 **4** 题。

| 难度 | 计划题数 | 已运行 | 已通过 |
|---|---:|---:|---:|
| 简单 | 3 | 3 | 3 |
| 中等 | 5 | 5 | 1 |
| 困难 | 2 | 2 | 0 |

目标测试（F2P）和原有回归测试（P2P）必须全部通过，才算解决一题。Aster 的 `verified` 只表示它自己的交付检查通过，不等于独立评分通过。

| 实例 | 难度 | 独立评分 | F2P | P2P | Aster 终态 | 已报告 token | 秒 |
|---|---|---|---:|---:|---|---:|---:|
| django__django-15382 | simple | 通过 | 1/1 | 161/161 | completed / verified | 247326 | 168.7 |
| sphinx-doc__sphinx-9658 | simple | 通过 | 1/1 | 24/24 | completed / verified | 623530 | 817.2 |
| sympy__sympy-13031 | simple | 通过 | 1/1 | 9/9 | completed / verified | 481115 | 553.4 |
| django__django-15503 | medium | failed | 2/2 | 72/78 | completed / verified | 469564 | 359.8 |
| django__django-15957 | medium | 通过 | 4/4 | 89/89 | completed / verified | 783046 | 590.0 |
| pydata__xarray-3993 | medium | failed | 0/2 | 2398/2398 | completed / verified | 228870 | 88.2 |
| sphinx-doc__sphinx-9229 | medium | failed | 0/1 | 13/13 | stopped / team_token_limit | 909194 | 300.7 |
| sympy__sympy-18199 | medium | failed | 0/1 | 113/113 | completed / verified | 141958 | 125.3 |
| pydata__xarray-6992 | hard | failed | 0/12 | 945/945 | completed / verified | 252854 | 130.3 |
| sphinx-doc__sphinx-7590 | hard | failed | 0/1 | 24/24 | failed / provider_error | 未知 | 78.2 |

最终 resolved rate：**4/10 = 40%**。

## 未通过的题

| 实例 | 本轮观察 |
|---|---|
| django__django-15503 | 修好了数字 JSON 键的目标问题，但让六项原有查询测试失败，属于修复引入回归。 |
| pydata__xarray-3993 | 完成 dim → coord 参数改名，但直接移除了旧参数；原始测试要求旧 dim 参数继续可用并发出 FutureWarning，兼容路径缺失。 |
| pydata__xarray-6992 | 修改 DataVariables.__len__ 后，题面中的报错消失；但 reset_index 的坐标、维度及索引状态仍不正确，十二项目标测试失败。 |
| sphinx-doc__sphinx-7590 | 首个主任务响应以 failed 结束（incomplete_response），工具执行 0 次、补丁为空。服务失败计入十题分母，不能据此判断该题的解题能力。 |
| sphinx-doc__sphinx-9229 | 未完成生产代码修复，达到 token 预算停止条件；原始目标测试仍失败。 |
| sympy__sympy-18199 | 修好了题面给出的零根示例，但原始目标测试还要求合数模数支持；补丁在该场景仍抛出 NotImplementedError。 |

已报告消耗合计 **4,137,457 tokens**；147/154 次供应商尝试有 total usage。该合计只包含已知值，未知部分仍保留为未知，不能作为完整账单。

每次运行最多 60 次模型调用、90 步工具、1,000,000 token、900 秒前台时间；记忆默认开启，各题独立。至多两题并行，耗时受共享主机和服务负载影响。

这十题是按上游专家修复时间分层、受可用运行环境约束的样本。结果用于观察当前 Aster 与该模型的表现，不能直接与完整榜单排名比较，也不能归因于某项记忆机制。

选题、评分口径及原始时间标签见 [协议](PROTOCOL.md)、[环境说明](ENVIRONMENT.md)和 [manifest.json](manifest.json)。逐题结果在 `results/`，候选补丁和脱敏轨迹在 `predictions/`；`predictions.jsonl` 使用 SWE-bench 标准预测字段，方便后续在官方容器环境复核。参考解及原始评分材料不在模型工作区。
