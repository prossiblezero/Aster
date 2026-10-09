# 评测与历史归档

这里存放 Aster 的实验协议、驱动、评分器、对照结果和运行证据。可运行的小项目放在 [exam_project](../exam_project/README.md)，教学网站放在 `learn_web/`。

## 历史归档

`archive/` 是只读历史材料。2026-09-18 将原 `exam_project/` 下八个实验目录整体迁入，保留全部 7,092 个文件，包括被 Git 忽略的本地证据与冻结源码；迁移前后逐文件摘要一致。详见 [迁移清单](archive-migration-20260918.json)。被忽略的本地证据仍不随普通 Git 提交上传。

原驱动、需求、评分器、报告与结果没有重写或重新计算实验身份。历史材料中的 `exam_project/...` 命令、绝对路径、冻结源码根和部分向外相对链接反映当时的位置，**不能直接当作当前启动命令**。移动位置不代表完成了一次新的评测；如需重跑，应在新的实验目录另行冻结协议和身份，输出新结果。不要在归档里覆盖候选、改写来源哈希或追加模型任务。

| 历史实验 | 报告 / 说明 | 独立产品 |
|---|---|---|
| Markdown CLI 与早期 Memory 对照 | [mdcheck_memory](archive/mdcheck_memory/report.md) | [mdcheck](../exam_project/mdcheck/README.md) |
| 空目录编码冒烟 | [long_task_smoke](archive/long_task_smoke/README.md) | [csvtotals](../exam_project/csvtotals/README.md) |
| 流式对照 | [stream_comparison](archive/stream_comparison/report.md) | [linecounts](../exam_project/linecounts/README.md) |
| 只读工具并行 | [parallel_comparison](archive/parallel_comparison/report.md) | [tagmerge](../exam_project/tagmerge/README.md) |
| 缓存短请求探针 | [cache_comparison](archive/cache_comparison/report.md) | 无，只有探针 |
| Memory 自然触发 | [memory_followup](archive/memory_followup/report.md) | [notetally](../exam_project/notetally/README.md) |
| Skills 发现与加载 | [skills_comparison](archive/skills_comparison/report.md) | [eventcount](../exam_project/eventcount/README.md) |
| 中断恢复与 profile 切换 | [session_recovery](archive/session_recovery/report.md) | [uniqwords](../exam_project/uniqwords/README.md) |

产品副本优先选择 off、nonstream 或 serial 条件；uniqwords 来自完成的第一阶段，不能把它解释为第二阶段 profile 切换成功。复制的实现、测试和示例保持原样，每个产品增加 `ORIGIN.md` 说明来源。

## 新实验

[2026-10-09 验证机制修复后 SWE-bench 重测](swebench-verification-20261009/REPORT.md)：修复版 `1c9ad94` 沿用原十题、模型和预算，各执行一次。官方结果仍为 Aster 2/10（20%）：SymPy 空稀疏矩阵题新增通过，原通过的 Django 预取题因供应商过载留下空补丁；pi、Codex 只引用历史 3/10，未重跑。九份非空补丁的已有测试均通过，但七份仍未通过目标修复测试。失败追踪有实际拦截记录，未证明整体得分提高；两次服务错误中止和一次超时均保留。

[2026-10-09 SWE-bench 五次故障补测](swebench-retry-20261009/REPORT.md)：对上轮五次因主机或模型连接故障留下空补丁的尝试各补一次，同源码、模型、镜像和预算。五份补丁均完成官方评分，Codex 的 django-15382 通过；Aster 两题、pi 两题未通过，其中 pi 的 django-15957 达到 900 秒上限。替换这五次结果后，十题成绩为 Aster 2/10（20% → 20%）、pi 3/10（30% → 30%）、Codex 3/10（20% → 30%）。原始结果及其消耗保留，其余 25 次原样沿用；这仍是十题子集结果。

[2026-10-08 Aster / pi / Codex 官方 SWE-bench 子集对比](swebench-compare-20261008/REPORT.md)：固定十题、简单/中等/困难 3/5/2，同模型 `gpt-5.6-luna`，每种 Agent 每题一次，共 30 次。官方 Docker 评分结果为 Aster 2/10（20%）、pi 3/10（30%）、Codex 2/10（20%）。解题阶段的主机卡顿和模型连接中断导致 5 次空补丁，全部保留；25 个非空补丁的官方评分均完成，评分阶段没有基础设施错误。本轮结果受到运行环境影响，不能用来做稳定排名，也不是完整榜单成绩。

[2026-10-08 SWE-bench Verified 十题子集](swebench-verified-20261008/REPORT.md)：按专家修复时间选取简单/中等/困难 3/5/2 题，当前 Aster 搭配 `gpt-5.6-luna`，每题单次尝试，通过 4/10（40%），分层为 3/3、1/5、0/2。使用原始 F2P/P2P 测试与本地隔离评分；包含一次首请求服务失败，全部失败保留。此为受环境约束的本地子集结果，不是官方 Docker 榜单成绩。

[2026-10-07 一日降耗与缓存检查](memory-cost-20261007/REPORT.md)：十题两组各一次，共 48 会话，记忆与后台整理开启。已报告总 token −39.9%、后续任务 −45.9%、未缓存输入 −36.8%；正常完成且通过均为 9/10，功能断言 30/30 → 27/30。CSV 漏存约定修正后单题补查 3/3，不替换主表。缓存输入占比 61.26% → 59.17%，不能称命中率提高；未知用量、研发消耗和新版完整请求归档缺口均保留说明。

[2026-10-06—07 自动学习与异步 Dream](memory-sleep-20261005/REPORT.md)：十题两组各一次，共 44 个会话；正常完成且通过 4/10 → 7/10，功能 4/10 → 9/10，约束 13/30 → 27/30。14 次保存回执均可对应实际条目。已报告总 token +12.6%，存在未知用量与基线服务失败，未证明降耗。冻结版漏填适用范围的失败保留，最终只做一次金额学习补查。

[2026-10-05 知识记忆十题对照](memory-knowledge-20261005/REPORT.md)：固定 10 题、每版 21 个学习/复用会话，严格复用成功 4/10 → 7/10，独立功能通过 5/10 → 8/10，约束通过 17/30 → 25/30，总 token 增加 1.7%。全流程均完成为 4/10 → 6/10；金额与用户纠正未保存的失败保留。另用两题补查提示和修复经验，支付案例主动沉淀及后续召回跑通，金额题仍失败；不同源码结果单列，最终准入提示修正仅做相关回归。

[2026-10-03 十题子 Agent 迭代](subagent-ten-20261003/REPORT.md)：保留原六题，新增四个固定源码调查任务，共 10 题、80 个行为事实，六轮 200 回合。最终自主委派 r5 严格通过 Solo 13/20、team 15/20；19 对完整用量 token 总量增加 17.3%，耗时配对中位数减少 19.0%。3/20 回合委派，2 次成功、1 次预算失败；全体行为事实 151/160 → 148/160，双方可解析为 143/152 → 148/152。满足原整题通过率与适度成本分支，不代表全指标优势；这些题用于开发，不是独立保留集或官方 benchmark 成绩。

2026-10-04 同组十题追加 r6 两遍、40 回合，累计七轮 240 回合：严格通过 Solo 11/20、team 13/20，总 token 减少 2.5%，但实际委派零次，token 提前收尾候选未触发。候选及回归补丁留档，该轮结束时保留 r5；详见上述报告，不将分数变化归因于未执行的机制。

2026-10-05 完成[44 回合受控续测](subagent-budget-controlled-20261004/REPORT.md)并采用 r6：全部回合实际派工，六个候选压力回合均触发提前部分交付；压力组严格通过 0/6 → 4/6，空答案 6 → 1，token 增加 10.2%。正常十题 7/10 → 8/10（可解析配对事实持平），充足预算前缀对照 3/6 → 4/6、token 基本持平。收益限定为本受控预算条件下的交付可靠性；Boltons 仍未严格通过，父方耗尽与子答案错误继续保留。相对 r5 两个生产文件净增 11 行，零新运行依赖。

[2026-09-28 多来源 Worker Code v2 真实模型对照](worker-code-20260928/REPORT.md)：冻结 5 来源、20 题，完成 solo / current_worker 共 40 次运行。独立通过率 70% 对 65%，Worker 全队 token 多 5.9%、耗时多 3.4%；18 个子任务仅 5 个正常交付。保留 COBOL 审查反馈正例、工具步数耗尽、验证与编辑障碍，未证明当前 Worker 整体受益。仓库原生适配与 Terminal portable 评分边界单独说明。

[2026-09-29 Worker 聚焦复验](worker-focused-20260929/REPORT.md)：同一修复后源码上的 4 道已见代码题各重复 3 次，共 24 个 episode。Worker 通过 5/12、Solo 2/12，但全队 token 多 21.6%、时间多 30.2%；CDF 首轮出现一次可确认的 scoped 写入协作，整体仍未证明稳定收益。

[2026-09-25—26 Worker 委派适用场景迭代](worker-value-20260925/REPORT.md)：五版轻量修正后，在两类仓库审计各两次的固定对照中，单 Agent 通过 3/4、委派通过 4/4，12 个子任务全部交付。观察到一次时区边界漏判的质量差异；全树 token 为 2.08 倍、耗时为 1.69 倍，未证明效率或稳定的上下文收益。保留全部 14 次运行、失败、人工复核和冻结证据。

[2026-09-25 Worker 交付与验证分工修复](worker-handoff-20260925/REPORT.md)：父任务具有严格测试要求时，子任务按实现交付，最终由父任务验证；保留首批失败与修正后的两题回归。最终两题通过，token 较上次委派批次下降 6.4%，仍未证明委派优于单 Agent。

[2026-09-22 GPT Worker 与跨会话 Memory 维护评测](worker-memory-20260922/REPORT.md)：真实模型完成模块维护、跨会话约定复用与协议迁移，比较自主选择/明确要求委派，以及关闭/基础/维护记忆的交付、查阅和完整用量；保留零委派、评分口径修订与未建立的维护增益。

[2026-09-24 Worker 等待修复复测](worker-memory-20260922/WORKER-REPAIR-20260924.md)：为 `worker_status` 增加有界等待并复测两道明确委派题；子任务完成数有所改善，但没有观察到端到端提速或成本下降。

[2026-09-21 回到历史位置重新探索专项](session-branch-scenario-20260921/REPORT.md)：围绕“完成 `i` 轮后经历 `j` 轮探索，再回到第 `i` 轮”的真实需求，比较旧版总结交接、旧版 checkpoint 与新版干净／带摘录分支的操作、交接成本、历史隔离和真实模型回答。

[2026-09-21 会话改造后的真实编码回归](session-quality-20260921/REPORT.md)：同模型、同预算、新旧冻结版本各执行 5 道仓库题 × 2 种工具集，记录任务完成率、独立代码验收、token 与耗时；与 [会话操作评测](session-ux-20260920/REPORT.md) 分开解释。操作便利性提升不必伴随模型能力提升，但仍需检查任务质量是否退步。

[2026-09-19 实验数据整理器修复复测](lab-followup-20260919/REPORT.md)：修复通用资料阅读与自测指引，补齐独立评分器，再让新旧冻结版本从隔离空目录完成同一道CSV→Excel任务；首次结果、服务故障、返修与成本分别记录。

[v0.2 工程收尾验收](v0.2-release/REPORT.md)：公开 CLI 从空目录开发实验数据工具和复习安排网页，真实 TUI 维护课业清单并验证取消、恢复和模型切换。保留框架干预、产品返修、独立反例及全部已知成本；只有验收合格的产品进入 `exam_project/`。

[2026-09-18 真实课业清单与机制复测](benefit-20260918/REPORT.md)：缓存阳性校准、网关观测、实际浏览器产品、Skills维护任务及Memory跨会话资料问答。协议、失败、评分器修订和所有条件单独保存；可运行成果见`exam_project/campus_planner`。

每轮实验使用独立目录，明确产品版本、任务需求、评分标准、对照条件和预算；用 aster 虚拟环境执行。机制是否触发、交付质量、性能差异分别记录，不能把单个案例的耗时或 token 差直接当作功能收益。

历史任务已经参与工程开发和诊断，只能作为审计材料，不能重新标成未见训练测试集。
