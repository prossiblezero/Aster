# Context state v10 实施记录

日期：2026-10-15。依据 [方案](../../docs/context-state-plan.md)完成 Stage 0–3；Stage 4 仅准备并冻结，未调用模型，尚无 v10 的任务通过率或线上成本结论。

## 实现

每次请求不再替换末尾动态消息。`workflow_state`、memory、技能目录和已加载技能正文分别按变化追加完整状态条目，进入持久历史；清空时追加 `[cleared]`，下次从历史中的最后条目判断有效值。预算充足时不再每轮注入倒计时，仅在 low/critical 阶段提醒。

压缩时移除旧状态条目并在末尾写入每个非空部分的完整值，之后恢复增量追加。回执计量以整个请求为锚；新增工具结果和状态条目都计入增量。`working_memory` 不再重复渲染，其存储、失效、Worker 摘录与 UI 展示均保留。

`context_built` 事件记录新条目数，sections 记录新条目的局部 token 数。策略版本为 `deterministic-v10`。无新依赖、生产配置项、服务或模型调用。

实际回归揭示了三处需要同步的调用方：会话树的恢复点摘要必须跳过状态条目，恢复 UI 不将其展示为用户发言，`scripts/benchmark_robustness.py` 删除过时的 working_memory 参数。未修改检查点字段或存储格式。

补充两项边界处理：先脱敏再比较当前值，避免同一敏感值每轮产生重复条目；进入完整条目模式即标记显式压缩，即使改写前后 token 数刚好相等。没有新条目时，其 token 计数为 0。

## 验证结果

基线是本轮开始时的 **HEAD 0ce78db + 已有 v9 未提交改动**，不是干净 HEAD。源码和测试已先备份到 `artifacts/private/context-state-20261014/baseline/`。所有差异文件都相对该备份生成。

| 检查 | 动手前基线 | 最终结果 |
|---|---:|---:|
| pytest 核心 | 473 通过 | 484 通过 |
| pytest 其余 | 814 通过、6 失败、4 跳过 | 816 通过、6 失败、4 跳过 |
| 合计通过 | 1287 | 1300 |
| 相对基线新增失败 | — | 0 |
| Ruff | 通过 | src、tests、修改的 robustness 脚本及本轮评测脚本通过 |

命令和耗时：[final-validation.json](final-validation.json)。完整日志：[核心](final-core.log)、[其余](final-noncore.log)、[Ruff](final-ruff.log)。[regression-comparison.json](regression-comparison.json)逐项核对了失败清单。仍失败的是原有 6 个小窗口用例：Responses/Chat 最新并行批次各 1 个，启用 Worker 后的 Responses/Chat 小窗口各 2 个。预算、输出保留及通过门槛未放宽。

新增 11 项状态条目测试覆盖 30 轮完整前缀、状态不变时零追加、预算分级、清空与分叉、压缩后恢复追加、Responses/Chat/Anthropic 工具结果后接两个条目、检查点恢复和脱敏。回执测试增加 2 项末项被替换/删除的回退检查。已有 40 轮测试仍要求且得到恰好 1 次压缩。

Worker `source_excerpts` 的测试文件未经修改并在全量测试中通过。[scope-check.json](scope-check.json)确认系统提示词函数、工具定义、缓存键传输、掩码函数、输出限长、工作记忆、Worker、检查点、TeamBudget 所在 schema、UI 工作记忆展示和交付收尾函数等受保护部分保持原样。

## 既有测试断言调整

以下调整反映持久条目的表示变化；原安全、原生协议、归档和预算约束仍保留。完整差异见 [tests.patch](tests.patch)。

| 文件 | 断言变化 |
|---|---|
| test_stable_prefix.py | 删除 working_memory 参数；改为全部前一请求项构成前缀；状态按部分追加，不含普通倒计时；history 统计包括末项；版本 v10。 |
| test_context_state.py | 从最后一条 workflow_state 更新读取状态，不假设它一定是请求末项；使用新的非指令标记。 |
| test_context_policy_input.py | 版本 v10；按类型找工具输出；分别检查 memory/catalog/skill 的 user 状态条目，不再假设全在同一末项。 |
| test_harness.py | 保留工作记忆更新检查，改为断言记忆摘要没有重复注入请求。 |
| test_receipt_metering.py | 新状态条目按完整新增内容收费，不再只加尾部长度差；补充最后一个锚项被替换/删除的回退测试。 |
| test_append_only_context.py | 比较完整前缀；按工具交换定位最新结果，仍断言 40 轮恰好 1 次压缩。 |
| test_context_provider.py | 保留原生推理/调用/结果的字节检查，允许其后有一个持久状态组；history 等于 prompt[2:]。 |
| test_context_cost.py | 保留原生交换与近期结果的检查，计入末尾的状态组。 |
| test_context_archives.py | 并行批次仍是一个完整工具组，之后有单独的状态组；legacy 历史只允许状态条目，仍不生成虚构工具调用。 |
| test_protocol_pairing.py | 无效模型调用后历史须等于已发送的历史；首轮合法状态条目保留，非法调用及文件写入仍禁止。 |
| test_runtime_lifecycle.py | abort 后历史须与请求发出时完全相等，仍禁止晚到工具调用写文件。 |
| test_session_continuation.py | 模型切换后的原生证据转换不变，允许预检写入一个状态条目；归档完整性断言保留。 |
| test_history_gateway_metadata.py | 从最后一条 assistant 消息检查 final_answer phase，避免将预检条目当成模型答案。 |
| test_review_regressions.py | 原生 reasoning/message/并行工具批次的完整性检查排除独立状态组，不再无条件去掉末项。 |
| test_skill_snapshots.py | 技能版本从最新 workflow_state 读取，正文从最新 active_skill_bodies 读取；仍验证旧源不重读、凭据脱敏和快照哈希。 |
| test_session_navigation_ui.py | 保留原有按用户请求搜索分支并恢复的断言；补充恢复界面不把状态条目显示为用户消息。 |

## 离线统计与评测准备

v9 的所有旧统计字段与冻结结果逐项相同。新增严格前缀口径得到 **360/360** 次后续请求改写末项；旧口径仍是 **2/360**。低命中数 **120/358**，前台未缓存输入 **2,782,173**。这些是对历史轨迹的复算，不是 v10 实测。

原边界模型默认输出完全复现原始结果：254/358 次预测精确命中，tail_straddle 90 次/1,537,698 未缓存输入。v10 分析直接引用该脚本，新增 `--append-only` 令尾部为 0 并检查完整前缀；不复制模型。原脚本会误将最后一项视为可替换尾部，且工具输出可能没有 content，因此该兼容改动是必要的。

[offline-checks.json](offline-checks.json)还验证：v9 的 383 个 context_built 事件能关联全部前台请求；严格改写 360、旧改写 2、低命中 120；合成工具输出末项可被边界模型分析；缺用量期间的末项改写仍被审计捕获；异常 flags 回执不参与估算比例。

[PROTOCOL.md](PROTOCOL.md)固定 old20、单 Agent、20 次任务尝试、6 并发、900 秒/题；只重跑 Aster，对照沿用 v9、旧 Aster、pi 和 Codex。缓存门槛、输入上限和至少 15/20 独立通过按方案第 7 节执行，含失败、超时和后台消耗，不补分或择优。控制环境使用已有含 Docker SDK/SWE-bench 的 Python，Agent 和网关继续使用项目环境。

源码差异：[source.patch](source.patch)；调用方和边界模型差异：[tooling.patch](tooling.patch)。冻结清单为 [manifest.json](manifest.json)，包含完整候选运行时、任务、执行/统计脚本、历史成绩、方案和关键验证证据。执行前运行 `evaluate.py verify` 重新检查。**本轮未运行 `run` 或 `gateway`，尚未产生 v10 benchmark 成绩。**

[冻结校验](freeze-check.json)通过：60 个产品文件、65 个运行时文件、453 个冻结输入，20 个唯一任务，工作区源码与快照一致，已启动尝试为 0。[控制环境检查](controller-readiness.json)确认 Docker/SWE-bench 可导入、Docker 可访问，20 题所用的 18 个不同镜像全部存在；该检查未创建容器或调用模型。
