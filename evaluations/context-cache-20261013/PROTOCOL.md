# Context cache v9：20 题验收协议

制定日期：2026-10-14。来源：[实施方案](../../docs/context-cache-plan.md)第 8 节。
状态：准备阶段；本目录的 `run` 命令调用模型，须在用户确认后执行。

## 固定对象

- 从 `general-coding-50-20261012` 原始任务中选择 `exposure == "old20"` 的全部 20 题，顺序按 `sha256("schedule:" + task_id)` 固定。
- 只运行 Aster，每题一个新会话、一次尝试；pi 和 Codex 使用同一批 50 题中这 20 题的既有成绩。
- Aster 基线为冻结的 `c6b651d`，独立通过 15/20。候选为当前 `0ce78db` 加本次修改，完整源码另存并记录 SHA-256。因此候选与历史基线的差异也包括已经合入的 MCP/会话改动，不能把所有变化单独归因于 v9。
- 复用 50 题的任务镜像、输入、依赖、运行器、网关及独立评分器；题目和测试不改。保存失败、超时、缺失用量，不重试补分，不择优。
- 模型 `gpt-5.6-luna`，Responses，medium，64000 上下文、8192 输出，900 秒/题，4 CPU / 8 GiB，6 个并发槽。原生 sandbox、严格验收、`max_workers=0`、自动记忆开启。
- 网关会将后台记忆请求也改为 medium/8192；延续基线这一限制。前台机制指标排除 `tools == [memory_changes]`，总消耗另行计入全部后台请求。
- 不追加付费 smoke；使用原有 50 题环境预检记录和本次本地回归。执行前验证全部输入与候选哈希；环境若变动，先做不调用模型的预检。

## 验收标准

| 指标 | Aster old20 基线 | 门槛 |
|---|---:|---:|
| rewrites / followups | 146 / 368 | ≤ 3% |
| rewrite_uncached | 2,787,012 | ≤ 250,000 |
| 非预期 prefix_drift 事件 | — | 0 |
| 改写对应 compacted 事件 | — | 全部对应 |
| stable_hit_mean | 0.718 | ≥ 0.85 |
| receipt 估算 / 实际，中位数 / p95 | — | ≤ 1.02 / ≤ 1.05 |
| cache_ratio | 0.470 | ≥ 0.78 |
| uncached | 4,356,424 | ≤ 1,300,000 |
| input | 8,213,832 | ≤ 9,450,000 |
| 独立通过数 | 15 / 20 | ≥ 15 |

前台请求数、总请求数、输出 token、缺用量、累计答题时间、正常交付且通过数一并报告。不把未缓存输入比例直接换算为金额。

14 题通过时保留原始 14/20，逐题分析回归；只有轨迹证明与上下文变更无关才能说明质量门槛例外，不能改写成绩。13 题及以下不通过。达标也不能据此宣称新 50 题成绩，完整重跑另行决定。

缓存命中受供应商影响；改写达标而稳定命中低于 0.80，只能说明缓存收益未验证，不能仅凭此证明缓存键是否被服务端采用。停止继续调整上下文策略，先定位传输及服务端因素。

## 复现命令

项目环境：`/home/dzh/.conda/envs/aster/bin/python`。以下命令均在仓库根目录执行。

```bash
# 只读归档；无模型调用。
/home/dzh/.conda/envs/aster/bin/python evaluations/context-cache-20261013/metrics.py artifacts/private/general-coding-50-20261012/runs --results evaluations/general-coding-50-20261012/results.json --exposure old20 --output evaluations/context-cache-20261013/baseline-old20.json
/home/dzh/.conda/envs/aster/bin/python evaluations/context-cache-20261013/replay_metering.py artifacts/private/general-coding-50-20261012/runs --output evaluations/context-cache-20261013/replay.json

# 冻结、验证；无模型调用。
/home/dzh/.conda/envs/aster/bin/python evaluations/context-cache-20261013/evaluate.py prepare
/home/dzh/.conda/envs/aster/bin/python evaluations/context-cache-20261013/evaluate.py verify

# 用户确认后才运行；20 次付费答题，沿用原生重试与后台记忆。
/home/dzh/.conda/envs/aster/bin/python evaluations/context-cache-20261013/evaluate.py run
/home/dzh/.conda/envs/aster/bin/python evaluations/context-cache-20261013/evaluate.py results
```

冻结清单包括候选源码、运行时、任务、原始输入、协议和本次脚本。已启动任务禁止重复尝试；部分运行可续跑尚未开始的任务，已有结果保留。候选撤销使用本次源码差异及 `artifacts/private/context-cache-20261013/baseline/`，不回退其他工作区改动、不覆盖旧评测目录。
