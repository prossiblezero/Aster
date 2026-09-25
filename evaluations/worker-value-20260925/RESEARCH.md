# Aster 委派机制：最新 GitHub 源码核对（2026-09-25）

范围：只读源码调研，未改 Aster 产品、未调用付费模型。固定抓取时的 HEAD，不将示例、官方文档、推测混为一谈。

## 固定来源

- pi 官方仓库 HEAD：`ff72faba28d10c86611863d0aaa5d3122f2d8cb0`；`git ls-remote` 与本地 clone 一致。提交标题 `fix(coding-agent): save new session file at the first user message`。它比此前参考的 `d5629e20489ccf770ed90b5a33941cb3b7ef24d0` 更新；本轮实际读新提交。
  - [subagent 示例扩展说明](https://github.com/earendil-works/pi/blob/ff72faba28d10c86611863d0aaa5d3122f2d8cb0/packages/coding-agent/examples/extensions/subagent/README.md)
  - [实际扩展实现](https://github.com/earendil-works/pi/blob/ff72faba28d10c86611863d0aaa5d3122f2d8cb0/packages/coding-agent/examples/extensions/subagent/index.ts)
  - [scout 提示](https://github.com/earendil-works/pi/blob/ff72faba28d10c86611863d0aaa5d3122f2d8cb0/packages/coding-agent/examples/extensions/subagent/agents/scout.md)
  - [worker 提示](https://github.com/earendil-works/pi/blob/ff72faba28d10c86611863d0aaa5d3122f2d8cb0/packages/coding-agent/examples/extensions/subagent/agents/worker.md)
  - 官方项目内 README 可直接核对；额外访问 `https://pi.dev/llms.txt` 返回 403，没有把未读页面当证据。
- OpenHands 官方 Software Agent SDK HEAD：`fcc102a697874d54a357e36004e02c95040dbdc0`；`git ls-remote` 与 clone 一致，发布提交 `Release v1.49.6 (#5319)`。
  - [当前 task 工具定义](https://github.com/OpenHands/software-agent-sdk/blob/fcc102a697874d54a357e36004e02c95040dbdc0/openhands-tools/openhands/tools/task/definition.py)
  - [task manager 实现](https://github.com/OpenHands/software-agent-sdk/blob/fcc102a697874d54a357e36004e02c95040dbdc0/openhands-tools/openhands/tools/task/manager.py)
  - [task executor](https://github.com/OpenHands/software-agent-sdk/blob/fcc102a697874d54a357e36004e02c95040dbdc0/openhands-tools/openhands/tools/task/impl.py)
  - [保留的 delegate 实现](https://github.com/OpenHands/software-agent-sdk/blob/fcc102a697874d54a357e36004e02c95040dbdc0/openhands-tools/openhands/tools/delegate/impl.py)
  - [code-explorer 定义](https://github.com/OpenHands/software-agent-sdk/blob/fcc102a697874d54a357e36004e02c95040dbdc0/openhands-tools/openhands/tools/preset/subagents/code_explorer.md)
  - 已读当前官方文档 [Task Tool Set](https://docs.openhands.dev/sdk/guides/task-tool-set)、[File-Based Agents](https://docs.openhands.dev/sdk/guides/agent-file-based)，实际下载其 `.md` 表示；本地保存在 `/tmp/aster-openhands-task-doc.md` 和 `/tmp/aster-openhands-filebased-doc.md`。

## 直接确认的机制

### 1. 等待应主要由运行时负责

pi `index.ts` 的 `mapWithConcurrencyLimit`（219 行起）和 parallel 分支（604 行起）在一个工具调用内部启动独立子进程、受限并发等待、最后一次返回所有子结果。上限 8 个任务、4 个并发；模型没有必要反复发状态查询。流式 `onUpdate` 报告 `done/running`，与父模型后续推理分开。

OpenHands 新 `task` executor 调 `TaskManager.start_task`，同步等待返回完成或失败。当前官方文档明确写 synchronous、parent blocks、TaskToolSet designed for sequential blocking tasks。因此不能把它宣传为默认后台异步机制。仓库同时保留的 `delegate` 工具接受任务字典，用线程并发执行并 `join` 后一次汇总；这与新版 task 的阻塞语义是不同 API。

Aster 当前的 `worker_start` 已异步启动，合理保留父任务可并行写集成测试的能力。可借鉴“一次等候一组交付”，无需复制 subprocess、线程池、Agent registry。

### 2. 主上下文只接收有用交付

pi `getFinalOutput`/`getResultOutput`（170 行起）提取最终文字或失败原因；parallel 结果对每个任务的模型可见输出上限 50 KB，完整记录在 `details`，并没有把所有子工具观察倒灌父上下文。`scout.md` 要求实际代码片段、精确路径/行范围、架构、从哪里开始；目的明确是另一个没看过文件的 agent 能使用。工具白名单随角色传入 CLI `--tools`，子任务不默认拥有父会话历史。

OpenHands task observation 只传 task ID、subagent、status 与最终 result/error；`TaskManager._run_stop_detail` 将非 FINISHED 终态当错误并保留 partial result。其 code-explorer 仅有 terminal 工具、限制只读行为并返回路径/行号/片段（这里描述的只读主要是角色提示，不能等同 Aster 的强制安全边界）。

Aster 已有独立运行上下文和 concise handoff 文案，主要瓶颈未必在“缺少 Agent 框架”。更该在多模块调查或长日志分析中衡量主上下文削减，同时检查最终证据覆盖率，不能只凭父 token 下降忽略全树 token。

### 3. 委派适用场景的选择比模型名字更关键

OpenHands `TASK_TOOL_DESCRIPTION`（112–130 行）明确说委派有 overhead；一个 grep/find/cat 能解决、自己已经有上下文或单文件编辑，都建议直接处理。详细任务包含文件路径、类名、错误信息和所需结果形式。它的 built-in general-purpose 工具集为 terminal/file_editor/task_tracker，code-explorer 只给 terminal，没有全套调度工具。

OpenHands 当前 prompt 还写“agent results are authoritative，只在判断/解释时验证”。这不是 Aster 应复制的部分：保留父任务最终真实验证、未验证报告、fresh-read 编辑要求。OpenHands 使用独立子 LLM 统计并汇入父统计；同一父会话的 `prompt_cache_key` 传给子会话是优化选项，不能保证具体供应商缓存命中，更不能直接推算账单。

## 建议的最小三步（按优先级）

1. **扩展现有 worker_status 的批量等待，不新增工具。** `worker_id` 保留字符串兼容，允许最多 4 个 ID；对目标 futures 一次 `asyncio.wait(timeout=...)`，随后复用单个 `status(id, wait_seconds=0)` 获取结果。返回按请求顺序的各项状态，聚合 changed_paths 仅用于父验证。明确 missing/failure/cancelled、超时不取消孩子、调用者取消应立即传播。模型只需一次批量发 start、完成自己的独立工作、一次 list wait。等待上限可放宽至 300 秒但用户中断保持可取消；这不是增加子任务步数预算。
2. **优先评测独立且上下文较大的调查/审查子任务。** 如 2–3 个独立子模块的行为追踪、多个服务配置风险核对，各子任务需多次实际源文件阅读，返回可由独立 grader 核验的结论与证据。solo 与 delegated 同模型/同题/同预算，记录正确项数、遗漏/错误项、全树模型/tool calls、全树 token、墙钟时间、主上下文保留的原始观察字数。委派好处可能是降低主上下文负担或并行关键路径，不预先承诺总 token 必降。原两道微型实现题保留为负对照。
3. **若仍有静态输入开销，再精简模型可见 schema 或角色初始工具集。** 不移除运行时权限校验，不更改持久化 schema，不加入角色配置框架。Aster 核心 9 工具当前紧凑 JSON 7,166 字符，递归删除无语义的 `title` 字段后为 6,590 字符，仅减少 576 字符（8.0%）；这不是主要胜负来源。工具削减可能省 token，但带来额外 tool_load 往返，必须用实际 trace 判断。角色工具策略 v2 即可，别与首轮 batching 混成无法归因的大改。

## 无需引入的东西

不为此复制完整子进程管理、Agent 注册/配置 DSL、角色路由、新模型供应商、task resume/persistence 协议，也不调高子预算来掩盖空转。Aster 现有权限收窄、同 workspace disjoint scopes、异常隔离和父验证契约保留。

调研资料位于 `/tmp/aster-pi-current-20260925` 与 `/tmp/aster-openhands-reference-20260925`。本笔记对源码提供设计启发，不声明来源项目公开性能指标能迁移到 Aster。

---

# Aster Worker 委派：论文调研与最小可用设计建议

调研日期：2026-09-25（Asia/Shanghai）。实际通过官方 ACL Anthology、NeurIPS 官网与 arXiv 页面/PDF 读取；不是凭模型记忆列举。范围是让有界委派在某类 coding-agent 任务中有可复现收益，不为项目引入论文级训练、图搜索或自动拓扑学习。

## 1. Towards a Science of Scaling Agent Systems

- 作者：Yubin Kim 等。
- 状态：arXiv 预印本，初稿 2025-12-09；本次读取 v3，2026-04-08。未取得已被 CCF-A 会议接收的证据，不当作正式会议论文。
- 摘要/版本：https://arxiv.org/abs/2512.08296
- 已读全文：https://arxiv.org/html/2512.08296v3
- 重点读了 §3.1、§4.3–4.5、§5、附录 F。
- 机制：控制工具、提示、预算，比较单 agent 与独立/集中/分散/混合四类多 agent；拆开“通信”（传消息）与“协调”（分工、状态监控、裁决）。研究将任务结构、协调开销、信息交换质量和成功率结合，而不是只数 agent 数量。
- 结果：260 组配置、6 个 agentic 基准、3 个模型家族；相对单 agent 的变化范围从可拆解金融任务 +80.8% 到顺序规划任务 -70.0%。多工具任务中的通信税、重复历史读取和顺序依赖容易抵消并行收益；有集中验证的结构更容易控制错误传播。
- 限制：SWE-bench Verified/Terminal-Bench 各只取 20 个实例，单元置信区间较宽；部分与工具数相关的回归项在 cluster-robust 检验后不再显著。因此不把“工具多必定失败”或某个阈值当普遍定律。
- 对 Aster 的意义：保留单 agent 作为正常默认；委派针对真正独立、局部信息量大、结果可以短消息汇总的工作。不能把两个很小的文件修改硬拆成 worker，然后期待普遍省钱。父 agent 最终验收保留。

## 2. Scaling External Knowledge Input Beyond Context Windows of LLMs via Multi-Agent Collaboration（ExtAgents）

- 作者：Zijun Liu、Zhennan Wan、Peng Li 等。
- 状态：ACL 2026 主会长文（CCF-A），正式 Proceedings Volume 1，2026-07，pp.10284–10314；由官方 Anthology 核实。
- 论文页：https://aclanthology.org/2026.acl-long.468/
- 已读 PDF：https://aclanthology.org/2026.acl-long.468.pdf
- DOI：https://doi.org/10.18653/v1/2026.acl-long.468
- 重点读了 §3.2–3.3、§4.1–4.3、§5、Table 1/4/6。
- 机制：把处理局部知识的 Seeking Agents 与整合答案的 Reasoning Agent 分离。Seeking Agents 各自阅读局部块；对消息按与查询相关性排序，在预算内同步最相关部分。Reasoning Agent 按 top-2^s 的节奏逐步加入消息并检查可回答性，避免把所有中间结果全量堆进统一上下文。
- 结果/实用结论：作者的消融中，移除 knowledge-accumulating reasoning 后，特别是输入增大时，表现明显下降；“开更多上下文”本身不够，还需限制返回给整合者的无关信息。论文在 QA 和长综述生成任务上测试，并不直接证明 coding agent 的改文件收益。
- 并行限制：文中某些 latency 曲线和“无限线程”的讨论是理想/复杂度分析，不是可直接搬到远端 API 的加速百分比。Table 6 的异构模型对比是在 4×A100、不并行条件下，不能用于声称 Aster API 委派会快 3.1 倍。
- 对 Aster 的意义：只借一个简单思想——worker 隔离局部探索历史，交付简明事实、文件/符号定位、依据与限制；父 agent 收到后整合，不重复读全部探索材料。本轮无需相关性模型、全局 worker 互聊或自适应指数消息框架。

## 3. AgentSlimming: Towards Efficient and Cost-Aware Multi-Agent Systems

- 作者：Yulang Chen、Haoxuan Peng、Jinyan Liu 等。
- 状态：ACL 2026 主会长文（CCF-A），正式 Proceedings Volume 1，2026-07，pp.30064–30086；由官方 Anthology 核实。
- 论文页：https://aclanthology.org/2026.acl-long.1387/
- 已读 PDF：https://aclanthology.org/2026.acl-long.1387.pdf
- DOI：https://doi.org/10.18653/v1/2026.acl-long.1387
- 作者代码：https://github.com/CitrusYL/AgentSlimming （本子任务只读论文，未审代码实现）
- 重点读了 §1、§3.2–3.2.1、§4.1–4.2。
- 机制：先裁掉冗余 agent，再把低贡献节点换成低成本模型；用结构指标、leave-one-out 贡献和实际成本潜力做排序；每步重测，达不到以固定原始基线为锚的质量门槛就回滚。可选 MCTS 微调不是必需阶段。
- 结果：论文对 AFlow 等较重 workflow 声称最高 78.9% 平均执行成本削减，部分任务准确率也升；这些数字来自压缩现有工作流，不能外推成“多 agent 比单 agent 省 78.9%”。正文成本 C 定义为每题 USD，摘要和部分表述称 token cost；如引用数字应保留“执行成本”、指出实际口径，不能当成固定模型纯 token 降幅。
- 对 Aster 的意义：最合适的简化是删除重复检索、父子重复验证、无意义轮询；先用相同模型检验机制，再讨论便宜 worker 模型。不要实现 RRF、Shapley 或图搜索。保留冻结的单 agent 基线，避免多轮小幅退化通过滚动基线被隐藏。

## 4. Why Do Multi-Agent LLM Systems Fail?（MAST）

- 作者：Mert Cemri 等。
- 状态：NeurIPS 2025 Spotlight Poster（CCF-A），已由会议官网核实；arXiv v3 为 2025-10-26。
- 正式会议：https://neurips.cc/virtual/2025/poster/121528
- 摘要/版本：https://arxiv.org/abs/2503.13657
- 已读全文：https://arxiv.org/html/2503.13657v3
- 作者代码：https://github.com/multi-agent-systems-failure-taxonomy/MAST （本子任务未审代码）
- 重点读了 §4、§5.2–5.3 和附录 failure categories/interventions。
- 机制：1600+ 真实 trace、7 种 MAS，建立 14 个失败类型，分为系统设计、agent 间错位、任务验证三组；不是只把失败归结为模型能力。包括重复步骤、角色偏离、不知道停止条件、信息未传全、忽视他人结果、过早终止和不完整验证。
- 结果：相同底座模型下改变系统工作流/提示可取得改善；但论文也强调仅靠局部提示修补无法解决所有失败。适合用作 Aster 的 trace 分类视角，无需引入它的付费 LLM judge。
- 对 Aster 的意义：每次迭代针对实际 trace 里最多的一两类浪费修复。结果里区分“worker 正常结束”“有用结果交付”“最终任务独立验收通过”，不能以退出 completed 代替验收。

## 给本轮的两点建议

1. **先选对需求场景，保持实现很小。** 用 2–3 个 worker 并行调查互相独立的真实子系统（例如持久化、工具权限、模型协议）；每个子任务需要局部多步检索/验证，而最终交付是可核对的证据摘要。交付格式至少含结论、文件/符号位置、相关条件或限制。父 agent 做一次整合与最终验证。无需互聊、完整历史共享、自动拓扑选择或训练。
2. **冻结验收与预算，成功后做小规模独立复验。** 同一仓库快照、同一用户问题、同一模型/工具能力/总 token 与总调用上限；单 agent 也允许批量 rg/read/shell，不故意禁用高效工具。最终事实/修改用预先编写的确定性检查器核验。可在独立模块上做“API/约束影响清单”或“多项独立故障定位”，不要人为填充无用文本来制造长上下文优势。任务通过后比较全体调用总 token、父任务输入上下文 token、总逻辑/HTTP 调用、工具调用、墙钟时间。主上下文减少是一个与“减少探索污染”需求直接对应的自定义指标，但必须并列披露全队 token 与成本，不能把成本挪给 worker 当成省钱。开发轮所有失败保留；新问题复验的胜率与旧问题调参后的胜率分开。

## 证据口径建议

- 有用证据交付率 = 通过独立校验的必需事实项 / 全部必需事实项；同时计无依据结论/错引文件，避免只增加回答长度。
- 父任务上下文负担 = 父模型各次请求 input token 总和，和峰值单请求 input token 分别报告；使用实际 usage，不用估算文本字符替代。
- 总成本 = 父 + 所有子模型的完整 usage；区分缓存输入/普通输入/输出，若无可靠实际单价就报告 token，避免伪造美元节省。
- 协调开销 = worker_start/status/send/stop 的次数；可辅以开始→首个有用证据和父整合耗时；它是诊断指标，单独下降不能证明比单 agent 有用。
- “在 X 类任务上更有用”允许表现为相同验收率下更低墙钟时间/总 tokens/父上下文占用，或同预算更高验收率；不能据很小样本宣称普遍领先。
