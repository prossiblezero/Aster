# 轻量委派调研与首轮决策

调研日期：2026-10-03。先扩至十题并冻结当前实现，再定位实际失败。论文里的收益只解释其自身实验，不能替代 Aster 的实测。以下阅读了实现或论文方法正文；会议身份另用一手页面核对。

## 实现参考

| 项目与固定版本 | 可直接确认的机制 | 对 Aster 的取舍 |
| --- | --- | --- |
| [pi scout](https://github.com/earendil-works/pi/blob/8ce69e9d2b171d173fe4b6b2b6256f1f4411e69d/packages/coding-agent/examples/extensions/subagent/agents/scout.md)、[subagent 扩展](https://github.com/earendil-works/pi/blob/8ce69e9d2b171d173fe4b6b2b6256f1f4411e69d/packages/coding-agent/examples/extensions/subagent/index.ts) | scout 给未读过源码的接收者提供精确行段、关键实现与连接关系；扩展限制并发、累加子用量，展示截断时保留完整输出。 | 保持交付可独立使用。避免为缩小提示而无声删掉支持结论的代码；证据仍有固定上限，超限时明确反馈。pi 的 JSON 事件传输不等于答案语义正确。 |
| [Codex delivery](https://github.com/openai/codex/blob/08b07eef1261b49b1071a8a4f60ed4d529d86a0d/codex-rs/core/src/agent/control/delivery.rs)、[spawn](https://github.com/openai/codex/blob/08b07eef1261b49b1071a8a4f60ed4d529d86a0d/codex-rs/core/src/agent/control/spawn.rs)、[wait](https://github.com/openai/codex/blob/08b07eef1261b49b1071a8a4f60ed4d529d86a0d/codex-rs/core/src/tools/handlers/multi_agents/wait.rs) | QueueOnly 与 TriggerTurn 区分送消息和触发工作；fork 显式处理历史及前缀基线；等待观察子状态并有时限。 | Aster 已有原会话续问、自动交付及 any/all 等待，继续复用；不引入第二套事件总线、全历史继承或角色框架。父方无独立工作时，减少无意义的唤醒比增加异步层更有价值。 |

这是固定源码结论，不冒充产品最新行为。OpenAI 与 Claude Code 官方子代理页面本次直接访问均返回 403，因此没有据旧 Claude Code 源码推断当前实现。OpenHands 当前 HEAD 已重组，旧 controller 路径返回 404；随后沿固定 README 的仓库职责表找到 software-agent-sdk，并阅读下文的 Task 实现，没有把旧路径当作最新实现依据。

## 论文机制与适配

| 论文、时间与状态 | 关键机制 | 采用或拒绝的原因 |
| --- | --- | --- |
| [BOAD: Discovering Hierarchical Software Engineering Agents via Bandit Optimization](https://iclr.cc/virtual/2026/poster/10009813)，ICLR 2026 主会；[全文](https://arxiv.org/html/2512.23631v2) | 把候选子角色作为 bandit arm；通过事后贡献判断估计 helpfulness，用 UCB 选择、CRP 扩展候选。避免仅凭团队成功给无贡献角色记功。 | 采用贡献归因：分别记录子错误、父方修正、父方破坏和正确交付保留。十题不足以训练可靠角色路由器，暂不引入角色池、训练或额外 judge。 |
| [LLM-as-Scheduler](https://aclanthology.org/2026.acl-long.581/)，ACL 2026 主会长文 | Agent 产出 envelope；先做格式、轻量判断和一致性检查，只有有必要时才调用较重的调度器，选择结束、验证、修复或路由。 | 先使输出契约和失败保存可靠，再按具体缺口补问；不对每个已经完成的子结果另起一轮 critic。论文节省是相对重工作流，不能当作相对 Solo 的收益。 |
| [AgentSlimming](https://aclanthology.org/2026.acl-long.1387/)，ACL 2026 主会长文 | 融合图中心性、成本与 leave-one-out 近似贡献，用 RRF 排序，逐步删节点或替换模型；质量阈值锚定原基线，失败即回退。 | 用完整质量与全队成本评价简化，考虑由父方保留一个独立工作包、只派一个孩子；不将父上下文下降单独包装为全队成本下降。两子节点无需图搜索框架。 |
| [MAS-ProVe: Understanding the Process Verification of Multi-Agent Systems](https://icml.cc/virtual/2026/poster/63666)，ICML 2026 主会；[全文](https://arxiv.org/html/2602.03053v2) | 比较 agent/iteration 两种验证粒度、judge/reward/PRM 与不同上下文策略。过程验证的收益不稳定，额外 judge 并不自动纠正不可解问题；上下文长短也有取舍。 | 父方直接对照源码核验具体值与契约，保留外部可执行 oracle；不新增常驻验证 Agent，也不用模型自称“已检查”作通过证据。 |
| [Routed Graph Handoff](https://arxiv.org/html/2608.25277v1)，2026-08-26 arXiv 预印本 | 类型化依赖图配合接收方解释约定，降低任务顺序歧义；路由器仅在依赖链任务上选图，需迭代回退的任务保留自然语言。论文消融显示单给 schema 不够，强制图格式可能退步。 | Aster 已有结构化 worker_finish 和自然语言任务。采用“发送与接收共同核对契约”的原则，保留灵活分工，不加入八种节点/七种边及路由模型。 |
| [SearchSwarm](https://arxiv.org/html/2606.09730v2)，2026 年 arXiv 预印本 | 独立子上下文；完整 brief 说明目标、已知事实、排除方向及其对总任务的作用；子方引用来源，父方保留方向判断与终止权。论文还通过 SFT 学习委派能力。 | 继续使用 parent_work、原请求、局部任务与源码摘录。改进父方核验责任，避免机械重述孩子的值。当前不做训练，也不把论文经 SFT 后的收益归于几句提示。 |
| [Finding Where the Buck Stops / DoCtOR](https://arxiv.org/html/2608.28264v1)，2026-08 arXiv 预印本；作者注记称 EMNLP 2026 主会接收，本次未独立核实 | ProFA 为步骤评分，定位最早的决定性错误及责任 Agent，再生成反事实修正、只让责任方反思，避免给正常角色灌入错误经验。 | 采用先归因再修复：保留孩子和父方原始结构化答案，按隐藏事实判断错误在哪个交付边界出现。不引入 PRM/PPO、自动长期记忆或训练服务。 |

另检索了 ICML/EMNLP/ECCV 的 2026 相关预印本注记；ECCV 检索到的 3D 理解工作与当前代码委派的直接关联较弱，没有为凑会议列表强行采用。ProgRouter 的作者注记是 EMNLP 2026 **Findings**，本次 Anthology 会议页尚不可取回，不记作已核实主会论文。

## 首轮实现选择

首个 Django 调查配对中，Solo 行为 8/8、严格 8/8，双子任务行为 7/8、严格 5/8。子方关于大小写映射的解释正确，但返回值层级错误；三条引用把多个符号拼进单符号字段。父方继承错误，没有独立重算。两个孩子合计四次证据超 4,000 字符拒绝，反复发送完整答案；不是接口故障。所有题仍继续运行，单对不作全局结论。

最小方案是沿现有交付工具和父方提示修正，生产只涉及 `context/prompt.py`、`tools/schemas.py`、`tools/workflow.py`：

1. 父方核对返回值的类型、层级、顺序、异常及指定引用格式；孩子交付前做相同的契约检查。只对具体缺口查源码或续问。
2. 完整已读证据总量从 4,000 提至 8,000 字符，仍最多四段，仍检查授权读取、真实行段与当前文件版本。提示优先选实现、少选长文档。常量由 schema 与执行逻辑共享，不增加用户配置。

拒绝自动截断支持代码：它可能恰好删掉边界分支，降低父方复核能力。也不自动拼接子 JSON 作为最终答案，因为这会绕过父方判断。通用结构化最终交付工具暂缓，待十题上的格式失败证明必要再单独试验。

脆弱假设：增加证据容量能省下的重试大于父方额外输入，且明确的核验提示能改善数据保真。若十题结果不支持，就撤回或调整，不能把工程测试通过当作模型效果改善。验证包括新增“4,000–8,000 字符完整交付”的旧版失败回归、8,001 上界及来源隔离等现有测试，再做同版 Solo 对照与完整重复。

父方＋一个孩子是后续可比较的最小调度方案：父方同时完成另一工作包，使用已有 parent_work 和异步交付；无需新增生产调度器。是否采用由完整配对结果决定。

## r5 的进一步取舍

r4 固定每题启动一个孩子仍未达标。重新读取 pi 已归档的 `index.ts:471` 起扩展入口：启用扩展后直接 `pi.registerTool({name: "subagent", ...})`，启用能力与模型调用分开。r5 据此在用户已有 `max_workers>0` 配置时直接展示启动入口，并删除工具及参数说明里与系统提示重复的文字；这不证明模型会正确选择委派。

AgentSlimming 方法正文中的质量阈值、成本信号与失败回退，继续作为整轮验收原则。r5 让原模型在既有能力内选择是否使用孩子，避免简单任务被驱动强制支付节点成本；没有增加路由器，也不是论文的图剪枝复现。验证须同时报告实际委派率、成功子交付、质量与全队成本。若全程没有子交付，即使分数变化也不能称为协作收益。

## 自主委派期间补读：ProgRouter 的边界

[ProgRouter](https://arxiv.org/html/2608.25992v2) 是 2026 年 arXiv 预印本，作者注记为 EMNLP Findings，尚未独立核实会议页。方法 §3.2–3.4 先把可观测的完成度、进展趋势和状态变化组合为进度分数，再用结构化与语义双路径回归器预测候选模型的边际进度；路由同时考虑剩余进度缺口、单任务预算和跨任务成本队列。它选择的是不同能力模型，不能直接等同于同一模型是否派孩子。

附录 D.3 明确需要在线探索、持久训练缓冲和预测器更新，主结果报告稳定后的任务流；D.2 的成本是 GPU 能量并含协调者推理，不等于供应商 token 账单。十题冷启动评测不适合移植这套训练系统。可取的是在真实错误、未验证边界或停滞处评估下一次协作的价值，而非仅按“有几个工作包”机械启动。若后续采用，也只考虑把已有状态与预算用于原模型的分工提示；当前 r5 已冻结，没有临时加入该机制，也不会据论文结果推断 Aster 的收益。

## OpenHands 当前 SDK 的执行取舍

沿 OpenHands/OpenHands 固定 README 的仓库归属表找到 [software-agent-sdk](https://github.com/OpenHands/software-agent-sdk/tree/84d4470acd85688b52c510532ffc83b234ebf453)。GitHub API 限流，改用 git ls-remote 获取提交，再下载该固定提交归档；SHA 和七份实际阅读源码登记于 SOURCES.json。

当前示例使用 [TaskTool](https://github.com/OpenHands/software-agent-sdk/blob/84d4470acd85688b52c510532ffc83b234ebf453/openhands-tools/openhands/tools/task/definition.py)。单次调用携带 prompt、类型和可选 resume，`TaskManager.start_task` 创建或恢复会话后同步运行，直接返回结果；与同仓旧 delegate 的先 spawn、再 delegate 两步接口不同。`_run_task` 将非 FINISHED 状态明确视为错误并保留部分输出，finally 中先合并统计再关闭活动会话；续问的子统计是累计值，用替换而非重复相加，避免双计。

它表明“父方暂时没有独立工作时，工具直接返回子结果”是一种可行的简单接口，能省去额外状态询问；Aster 已有异步父工作和状态等待，不能不顾并行场景直接改成全部阻塞。当前不新增第二套 TaskManager 或角色文件系统。后续若证据支持，可考虑只在无独立父工作时复用原等待路径；需要明确超时、取消、恢复和完成门禁。

工具描述将结果视为 authoritative、只在涉及判断时复核，这不是 Aster 可以照搬的保证：现有轨迹已经出现子报告值或引用错误。可借鉴的是具体交付物、路径/行号/代码片段，以及按缺口核验；继续保留来源与权限检查，不用提示宣称代替证明。code-explorer 的终端只读限制在所读文件中是提示约束，此处不据此推断其完整沙箱安全性。
