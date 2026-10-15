# v11 减少轮次：两轮冻结评测协议

制定：2026-10-16。依据 [实施方案](../../docs/turn-reduction-plan.md) 第 7 节。冻结后不修改本协议、执行器与指标定义。准备不调用模型；每一轮分别取得用户确认后运行。

## 对象和环境

- old20 为 general-coding-50-20261012 中 exposure=old20 的原 20 题；题目、提示、镜像、独立评分器和依赖保持原样。只重跑 Aster；pi、Codex 使用历史成绩。
- 第一轮源码取 `v11-stage2`，第二轮取 `v11-stage3`。用 `git archive <tag> src/aster` 导出独立运行时，逐文件 SHA-256 冻结，与 checkout 对应标签的源码字节一致；运行评测脚本保留在本分支。第一轮绝不使用当前分支的 Stage 3 源码。
- `gpt-5.6-luna`、Responses、medium、64000 上下文、8192 输出、900 秒/题、4 CPU / 8 GiB、6 槽并发、每次空会话、严格验收、原生 sandbox、max_workers=0、自动记忆开启。原生请求重试与后台记忆保持原样；网关沿用 medium/8192。
- 使用 general50 已验证的任务环境。运行前校验镜像存在、全部冻结输入/运行时/外部二进制哈希；环境变更时先做无模型环境预检。
- Stage 0 为通过密钥扫描，将历史 `aster_episode.py` 的固定 dummy key 写成相邻字面量。评测仍使用原冻结 runtime 内的原始脚本，原 SHA-256 不变；准备时另外检查这两份 Python AST 完全一致，清单记录此归档字节变化。其他历史输入必须匹配原哈希。

## 题目分组与次数

每轮：old20 各一次（attempt 1），六道波动题各再两次（attempt 2、3），共 32 次任务尝试。

恒过 10 题：django__django-17029、sympy__sympy-23262、sphinx-doc__sphinx-9230、pydata__xarray-7233、pydata__xarray-3677、pytest-dev__pytest-7571、psf__requests-2931、terminal-polyglot-c-py、aider-simple-linked-list、aider-sgf-parsing。

恒败 4 题：django__django-11400、sympy__sympy-17630、sphinx-doc__sphinx-11510、pytest-dev__pytest-10356。

波动 6 题：terminal-cancel-async-tasks、terminal-custom-memory-heap-crash、terminal-schemelike-metacircular-eval、terminal-kv-store-grpc、terminal-cobol-modernization、psf__requests-5414。

- 原始数据路径：`artifacts/private/turn-reduction-20261015/round<N>/attempt<M>/runs/aster-<task>`。不同轮/尝试目录隔离，使用运行锁避免重复启动；已完成任务跳过，已启动但无结果的任务拒绝重跑。派生统计可离线重新生成，原始目录不得覆盖。
- 若恰有一道恒过题 attempt 1 失败，允许对它追加 attempt 2、3，必须两次都过才满足稳定质量门槛。除此之外不追加尝试或挑选最好成绩，不把重试结果合回 attempt 1 通过率。
- 连续六道任务仅出现供应商故障时停止提交后续任务，保存异常和未完成状态；未完成/未评分不算通过。后台记忆、失败、超时、缺用量都记录。

## 第一轮门槛（Stage 1 + 2）

| 指标 | v10 | 要求 |
|---|---:|---:|
| 恒过题 | 10/10 | 10/10，或恰一道失败后补两次且两次均通过 |
| 恒败题 | 0/4 | 只报告 |
| 波动题 × 3 次 | 历史 21/36 | ≥ 8/18 |
| fresh_read_blocked | 11 | ≤ 2 |
| reread_only_turns | 27 | ≤ 5 |
| search_only_turns | 21 | 报告 |
| old20 attempt 1 turns | 358 | ≤ 325 |
| 前台有用量请求 | 378 | 报告 |
| cache_ratio | 0.885335 | ≥ 0.85 |
| strict_rewrites / followups | 4/358 | ≤ 3% |
| 加权成本 | 约 3.52 | 报告 |

第一轮未通过就停止；不运行第二轮。是否 revert Stage 1/2、回到 v10-baseline，由用户决定。

## 第二轮门槛（Stage 1 + 2 + 3）

第一轮通过，且用户再次确认后，才运行第二轮的 32 次任务尝试。

| 指标 | 要求 |
|---|---|
| 三类题的质量 | 同第一轮 |
| 两道 aider 题被接受的 pre-existing 声明 | 0（检查所有尝试的完整轨迹） |
| requests-2931 + requests-5414 的 turns_while_failing | ≤ 8（v10 为 22，取 attempt 1） |
| 所有被接受声明 | 逐条列出 ID、命令、基线结果/工作区哈希/证据 ID，人工核对与任务无关 |
| old20 attempt 1 turns | 不高于第一轮 |

声明人工核对记录写 `preexisting-review-round2.json`，以报告的 `review_id` 为键，值含 `unrelated: true/false` 与具体 `reason`。没有核对记录不会自动算通过。这里核对的是任务要求，不把“基线上失败”自动等同于“可以忽略”。

第二轮质量失败时执行 `git revert v11-stage3`，保留 Stage 1、2；其他门槛失败如实报告，不宣称收益。

## 指标和报告

- 成本、token、缓存和轮次门槛仅统计 attempt 1 的 20 题；重复尝试仅用于质量，全部额外消耗也保留在逐次 results 中。
- 前台排除工具列表恰为 `[memory_changes]` 的后台记忆请求。有用量请求按 input_tokens > 0；缺用量单列、不估填。
- 加权成本 = `(未缓存输入 × 1.25 + 缓存输入 × 0.125 + 输出 × 10) / 1e6`，只作相对比较，不是账单。
- turns / turn_kinds / 重读 / 搜索 / 门禁 / 带失败轮沿用最后前台请求 input 的 v10 口径；压缩掉的历史和最后响应不在此轮次数中。纯重读轮按轮开始时刚编辑未重读的文件集合计算，同轮分段读取同一文件计一轮。固定基线为 `baseline-turns.json`，必须复现全部九项数值。
- `preexisting_declared` 统计最终 input 中的接受声明；完整声明审计另读 `.aster.tar` 所有 tool_result 事件，以免压缩遗漏。逐条列出证据；声明不会把真实失败改记为测试通过。
- 沿用 v10 的完整前缀审计、请求/轨迹关联和 policy_version=deterministic-v10 检查；任何新出现的不明改写/缺失轨迹要调查，不掩盖。格式错误、哈希/来源不一致直接阻止验收。
- 每轮生成 `REPORT-round<N>.md` 与 `audit-round<N>.json`：门槛表、逐题逐次结果、v10 指标对比、全部请求异常、源码标签和提交。无分母或数据缺失时不算门槛通过。

## 命令

仓库根目录运行。Python 控制环境和 Agent 环境与 v10 相同。

```bash
# 无模型：验证脚本、冻结和校验两个源码版本。
/home/dzh/.conda/envs/aster/bin/python evaluations/turn-reduction-20261015/offline_check.py
/home/dzh/.conda/envs/aster/bin/python evaluations/turn-reduction-20261015/evaluate.py prepare --round 1
/home/dzh/.conda/envs/aster/bin/python evaluations/turn-reduction-20261015/evaluate.py prepare --round 2
/home/dzh/.conda/envs/aster/bin/python evaluations/turn-reduction-20261015/evaluate.py verify --round 1
/home/dzh/.conda/envs/aster/bin/python evaluations/turn-reduction-20261015/evaluate.py verify --round 2

# 仅在第一轮确认后，顺序执行；每次 run 后导出统计。
/home/dzh/Aster/artifacts/private/swebench-compare-20261008/controller-env/bin/python evaluations/turn-reduction-20261015/evaluate.py run --round 1 --attempt 1
/home/dzh/.conda/envs/aster/bin/python evaluations/turn-reduction-20261015/evaluate.py results --round 1 --attempt 1
/home/dzh/Aster/artifacts/private/swebench-compare-20261008/controller-env/bin/python evaluations/turn-reduction-20261015/evaluate.py run --round 1 --attempt 2
/home/dzh/.conda/envs/aster/bin/python evaluations/turn-reduction-20261015/evaluate.py results --round 1 --attempt 2
/home/dzh/Aster/artifacts/private/swebench-compare-20261008/controller-env/bin/python evaluations/turn-reduction-20261015/evaluate.py run --round 1 --attempt 3
/home/dzh/.conda/envs/aster/bin/python evaluations/turn-reduction-20261015/evaluate.py results --round 1 --attempt 3
/home/dzh/.conda/envs/aster/bin/python evaluations/turn-reduction-20261015/audit.py --round 1
```

`--tasks ID [ID ...]` 可选择尚未启动的任务，或符合条件的唯一恒过失败题的 attempt 2/3。未传参数时 attempt 1 选择 old20，attempt 2/3 选择六道波动题；所有组内顺序固定为 sha256("schedule:" + task_id) 排序。第二轮需新的确认，再将上述 `--round 1` 改为 `--round 2` 执行；程序也校验第一轮已通过。
