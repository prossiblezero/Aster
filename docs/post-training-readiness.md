# 后训练准备度审计

审查日期：2026-09-18。依据当前源码、数据集约定和现有回归测试；本文件是方案设计依据，不表示已实现训练算法。

**Aster 已有足够基础并行开展数据集、SFT 和 GRPO 的方案设计，也能支持小规模外部策略 rollout；目前不放行直接开训。** [v0.2工程收尾与有限发布验收](lightweight-agent-v0.2-plan.md)仍须如实记录完成与失败，不能用设计讨论替代产品验收。无须先照其他Agent重写核心，也不把继续增加工程功能作为训练设计的无限前置条件。

2026-09-18 实施补充：[后训练方案与数据契约](post-training-design.md) 已补齐可执行的任务隔离、实际请求到 SFT 投影、assistant-only mask、GRPO 版本/概率/分组/预算校验。新模型回复绑定实际请求摘要，旧缺少绑定的证据仅作审计，不能直接导出。尚未采集新训练集、接入真实 tokenizer 或启动训练。

**2026-09-18 真实产品复测补充：可以继续设计数据集、SFT与GRPO，但不据本轮结果立即开训。** [课业清单实测](../evaluations/benefit-20260918/REPORT.md)中，原三次基线尝试均未完整交付，须保留服务故障、恢复及后续交付的全部轨迹和成本，不能只留下成功样本。网关出现`completed`但`output=[]`；[缓存探针](../evaluations/benefit-20260918/cache/report.md)还观察到相同请求的响应模型标签和usage计数变化。采集需同时记录请求身份、实际响应模型、脱敏响应结构、原始与归一usage及覆盖率，区分服务失败与策略错误；模型别名不能代替SFT教师来源或GRPO行为策略的不可变版本。

同一候选仅修正评分器就从8/12变为12/12，说明奖励器必须先验证正负样例、协议边界和浏览器基础设施，版本化保留原评分与更正，不能直接把误判用于筛选示范或给策略负奖励。[Memory手种问答](../evaluations/benefit-20260918/memory/report.md)确认召回可用，却没有来源读取或token收益，不能转述为自主学习或后训练有效。下一步按新的仓库与任务家族预先划分train/dev/test，先冻结未见测试集再采集；本轮产品、公开规则、问答和调试过的评分场景只作开发诊断，不作为泛化测试，也不通过改数值、措辞或机制开关伪装成新家族。下文历史审计保留。

## 本轮工程收尾后的状态

**2026-09-19 补充：** [实验工具同题诊断](../evaluations/lab-followup-20260919/REPORT.md)补齐依赖丢弃和正式测试入口说明，最终核心与网站959项离线回归通过。两组首测均未完整交付（22/24与20/24）；补齐反馈的新版本另经一次明确缺陷返修，独立产品24/24、正式真实CLI自测5/5及干净安装/交互运行检查通过。该结果证明此失败产物可在有限反馈下修复，不证明首次自主交付或机制普遍收益。保留首次失败、服务中断、操作员错误和追加成本；本题仍属开发诊断，不转作未见测试。方案设计可以继续，批量采集/开训条件不因此自动放行。以下v0.2记录保持其历史语境。

Provider有界重试及脱敏诊断、CLI流式进度与TUI预算/恢复提示、checkpoint/inflight不确定副作用拒绝续跑、工具schema错误反馈、只读MIME元数据和受控pip临时缓存均已落地。核心回归954项通过。三个真实产品场景尚未全部通过：数据整理器原16/16遗漏公式样式异常单元格和文档正文读取缺口；维护产品32/32及取消恢复通过，首次模型切换失败，经`7adc9e1`修复及单次真实TUI补验后成功；原失败保留。lab两项剩余门槛未因此放行，整体验收不是全部通过。

[复习安排器](../evaluations/v0.2-release/study-delivery.json)初评8/9，唯一产品返修修复空课程重排，并让Node自测实际执行`app.js`及DOM删除/生成/重新加载路径；最终独立验收9/9，自测2项通过，候选字节已交付。累计42步、36轮、1196.469秒，保留最初框架schema故障、3次传输超时与返修，未报告的超时usage仍未知。这证明该任务在明确澄清和有限返修下能交付，不证明一次自主成功、通用能力或Aster自主浏览器验证；其题目与轨迹仍只作开发诊断。

这些修复改善训练前的执行和证据契约：每个逻辑请求的传输尝试有上限，尝试身份、终态分类、响应结构与已报告usage可追踪；完整JSON里的工具schema错误在任何副作用前拒绝整批，作为可纠正观察回给策略；结果不明的工具调用不能因恢复或采纳工作区而被悄悄重放。它们不能保证上游服务稳定，也不能把网关model标签升级成可信权重身份。若状态只能确认“工具效果可能已发生”，该段应作为中断/人工检查记录处理，不当作正常可继续的训练转移。

新会话默认是单Agent与核心工具：Memory、evolution、auto_dream关闭，`max_workers=0`；只读并行仍默认1。Memory和Skills保留可选机制，能力指引与不可信内容已分开，Memory只收录可验证来源摘录，Dream只报告实际维护结果。本轮验证的是开关、来源、快照、失效和事件契约，没有新增Memory/Skills的因果收益对照，也没有新采集训练集或启动SFT/GRPO。首版训练可沿用关闭Memory/Worker的最小配置；Skills是否提供及其版本在阶段A明确，不把技能自动发现误写成新增的默认关闭开关。

剩余路线按最小边界冻结：三条公开入口任务已完成有限验收，保留lab未过门槛以及所有首次结果、服务故障和返修；下文阶段A的方案设计可并行进行。批量采集及开训仍须有可信的模型身份、冻结任务划分、经正负检查的奖励器和输入/输出对齐。发布验收用过的题目、clarification、操作轨迹及调试过的评分器继续归开发诊断，不进入未见测试集。若某门槛失败，先报告具体失败与版本，不以重构Runtime、增加Memory算法或重复同题直到成功替代门槛。

## 已有基础

| 能力 | 当前已实现的行为 | 源码与检查入口 |
| --- | --- | --- |
| 独立 episode | `AsterEnv.reset()` 按工作区规则复制种子仓库到临时目录；上一轮改动不会变成下一轮起点 | [rl.py：AsterEnv.reset](../src/aster/rl.py)、[test_runtime_repairs.py](../tests/test_runtime_repairs.py) |
| 统一执行与观察 | 外部动作复用 `Runtime.step()`；`model_input` 与工具定义来自自主运行同样使用的 `prepare_request()` | [rl.py：_step / _observation](../src/aster/rl.py)、[runtime.py：prepare_request](../src/aster/core/runtime.py)、[test_context_policy_input.py](../tests/test_context_policy_input.py) |
| 有界交互与终态 | 工具动作和 Final 消耗预算；达到上限仍评分最后候选；终态拒绝重复 step，评分前等待 Worker 停止 | [rl.py：_step / _terminal_reward](../src/aster/rl.py)、[test_rl_contract.py](../tests/test_rl_contract.py) |
| 独立奖励入口 | 完成要求检查与外部 verifier 分开；没有独立 verifier 不给成功奖励，仓库测试结果只是观察 | [rl.py：_terminal_reward](../src/aster/rl.py)、[schema.py：VerifierResult](../src/aster/schema.py) |
| 轨迹与输入证据 | 保存实际请求 payload、上下文策略版本、工具结果归档、调用历史、checkpoint 和代码差异；检查工具调用与结果配对 | [runtime.py：prepare_request / checkpoint](../src/aster/core/runtime.py)、[messages.py：history_groups](../src/aster/messages.py)、[evidence.py](../src/aster/storage/evidence.py) |
| 失败与恢复边界 | 生成尝试有界、分类诊断与已报告用量累加；schema错误形成配对的失败观察；本任务及已启动子任务有未结在途调用时拒绝恢复 | [provider.py](../src/aster/providers/client.py)、[runtime.py：_execute_batch](../src/aster/core/runtime.py)、[session.py：assert_settled_tools](../src/aster/sessions/checkpoint.py) |
| 任务与评分组织 | 任务有初始 fixture、预算、参考动作和独立检查；真实模型与脚本回放分开统计，保留失败尝试 | [datasets/README.md](../datasets/README.md)、[benchmark_support.py](../scripts/benchmark_support.py)、[benchmark.md](benchmark.md) |

这些基础足以开始定义“在什么任务上采样、模型看到什么、动作如何执行、最终如何评分”。它们不证明某套训练方法已经有效，也不等同于训练数据已经准备好。

## 真正训练前需要补齐的边界

### 1. 新建训练、开发与测试清单

现有数据集明确标记为 `evaluation`，其参考动作和已有模型轨迹不直接进入后续训练。见 [public_benchmarks.md](../datasets/public_benchmarks.md)。训练数据应另行采集，并按仓库、问题家族划分；同题改变量名、数值或措辞仍归同一家族。

清单至少记录任务来源与许可、仓库版本、初始文件、环境依赖、验证器版本、难度标签和划分。先验证参考解与失败基线，再采集轨迹；不能把预算档位、重复采样和机制开关当作新增任务。

本轮网页案例以及被用于调试 Harness 的 Memory 实验可以作为开发诊断或机制评测证据。若已根据结果修改实现、提示或预算，就不应称为从未使用过的独立测试集；也不能一边训练这些轨迹，一边用同题成绩宣称泛化。新的测试清单应在后续选择模型、提示和训练参数之前冻结。

早期真实 Memory 实验结果参见 [mdcheck 报告](../evaluations/archive/mdcheck_memory/report.md)；本轮新实验见 [notetally 报告](../evaluations/archive/memory_followup/report.md)。两者均已用于工程诊断，不能充作未见训练测试集。

### 2. 将审计工件转换成可校验的 SFT 样本

`scripts/benchmark_support.py::archive()` 复制运行工件和候选文件；它不是训练样本导出器。新增 `training.py` 已有实际请求投影和 assistant-only mask 的校验契约，尚无真实 tokenizer/template 对齐、轨迹筛选及训练数据流水线。

导出应以每次模型请求的实际可见输入与对应 assistant 输出为单位。上下文裁剪和归档回读会改变观察，不能仅用最终 checkpoint 重建所有历史时刻，也不能直接把 `state.messages` 当成完整模型输入。`Runtime.prepare_request()` 保存的请求工件和完整执行历史应分别保留，前者用于训练输入对齐，后者用于审计与协议检查。

需要明确目标模型的 tokenizer、chat template 和工具调用格式，校验 tool call ID 与结果配对，生成只监督目标 assistant 输出的 mask。system、user、工具观察及隐藏测试答案不作为输出标签；供应商的加密 reasoning 或签名字段不能直接变成训练目标。成功轨迹筛选后仍保留原始失败记录，避免事后只展示成功案例。

外部 `AsterEnv.step()` 当前接收解析后的 `ToolCall` 或 `Final`，并不保存外部策略生成这些动作时的完整 token 序列。训练适配器应在解析前记录真实输出，明确解析失败和不完整生成的处理方式；不需要为此另造一套工具执行器。

### 3. 为 GRPO 接入可训练策略与采样元数据

当前 `ModelClient` 是推理协议适配器，`ModelReply` 提供文本、工具调用、usage、模型名及原始协议记录。没有权重更新、逐 token 概率、优化器或模型检查点管理接口。向现有模型网关反复发请求不能直接训练该网关背后的模型；网关可按单独的数据方案提供教师示范，训练仍需可更新权重的目标模型。

GRPO 方案需要定义同题多次采样的分组、组内奖励与优势计算，并由训练侧获取或可重算行为策略的逐 token logprobs。还应记录 actor/reference 权重版本、token IDs、采样参数、输出终止原因及训练 mask，保证计算概率时使用的观察与采样时一致。

当前请求工件中的 `policy_version` 来自 `ContextReport`，表示上下文构建规则，当前为 `deterministic-v7`；它不是行为模型的权重版本。此次宿主能力指引变化已更新该版本，采样和训练输入应保留各自实际版本，不把旧工件事后改写成新prompt。模型名称也不能替代不可变的训练检查点标识。相关契约见 [schema.py：ModelReply](../src/aster/schema.py)、[provider.py：ModelClient](../src/aster/providers/client.py)、[context.py：ContextReport](../src/aster/context/budget.py)。

### 4. 明确奖励可信范围与异常处理

现有奖励入口已有良好约束：独立 verifier、完成要求校验、预算到期评分、终态保护和评分前停止 Worker。当前中间动作代价按 `max_turns` 归一化，终局采用 `+1/0/-1`；这是可讨论的初始奖励契约，不是已经验证有效的训练目标。

隐藏评分将候选复制后再加入固定测试，关闭候选 `conftest.py` 和第三方 pytest 插件自动加载，并核验结构化报告。但候选 Python 与 pytest 仍处于同一解释器，不能宣称对主动伪造评分的策略完全抗作弊。当前边界适合可信任务集上的小规模试验；若训练策略会主动探索评分漏洞，需要进一步隔离候选执行进程与可信断言进程。

训练方案还应区分候选错误、基础设施超时和 verifier 自身故障，记录奖励版本并检查异常高分轨迹。不能把模型自报完成、公开测试通过或 stdout 中的 `passed` 当成唯一奖励。依据见 [benchmark_support.py：external_check](../scripts/benchmark_support.py) 和 [grader-boundary.md](grader-boundary.md)。

### 5. 统一动作、随机性与整个任务的预算

外部 `AsterEnv.step()` 每个 ToolCall 或 Final 算一次决策轮次；自主 Runtime 的一次模型回复则可能包含多个工具调用。首版训练需要明确选择单调用动作契约，或另行定义批次适配，不能直接把两者的轮数当成相同单位。

`reset(seed=...)` 目前只把 seed 返回到 info，没有设置模型采样或依赖库的随机状态。可复现实验仍需训练器控制采样种子，固定任务、模型、工具与上下文版本，并记录环境差异。

Worker默认关闭；显式开启后各有12步、12轮预算，`max_workers`限制同时存活数量，仍没有整个父子任务共享的token、耗时和动作成本上限。本轮补充子任务在途检查不代表已经实现Worker恢复或训练信用分配。首版SFT/GRPO维持关闭Worker，先验证单Agent学习闭环。需要训练委派策略时，再加入整个任务的预算、父子轨迹关联和奖励归因，不能只统计父运行成本。真实任务验收控制器的累计账本也不是已实现的训练侧共享预算。依据见 [rl.py：reset / _step](../src/aster/rl.py)、[workers.py：WorkerManager.start](../src/aster/workers.py)。

上述事项是从推理 Harness 接入训练的工作，不应写成现有功能已违反契约的 bug。

## 建议的分阶段下一步

| 阶段 | 具体产物 | 进入下一阶段的条件 |
| --- | --- | --- |
| A：冻结任务与接口方案 | 新 train/dev/test 清单；任务来源、环境与验证器；单 Agent 动作和预算口径；目标模型及训练范围 | 划分无仓库/问题家族泄漏；参考解与失败基线可复现；测试清单封存 |
| B：少量新任务采集与 SFT 数据验证 | 新任务教师轨迹；按实际请求导出的训练样本；格式、配对、脱敏与 assistant-only mask 检查；训练前基线 | 能人工核对样本输入/输出，失败与筛选理由可追溯；完成一次小批次训练并确认只更新预期标签 |
| C：SFT 小规模对照 | 固定开发集上的训练前后正确率、工具合法率、成本与失败分类 | 排除模板或导出错误造成的退化；保留完整实验记录，不据测试集反复调参 |
| D：GRPO 最小试验 | 可训练 actor；同题分组采样；概率与版本记录；独立奖励；明确终止和异常策略 | 采样与训练输入一致，奖励存在可学习差异；单组更新可复现且无已知奖励投机；随后才扩任务和并发 |
| E：可选的长任务与委派训练 | 更长任务、整个任务的共享预算、子轨迹及信用分配 | 单 Agent 闭环已有稳定收益，且实验证明新增复杂度有必要 |

SFT 与 GRPO 的详细算法和数据规模应在阶段 A 决定。这里给出的是实现顺序及验收条件，不预设二者都必须开展，也不把重写 Runtime 作为前置条件。

## 验证记录及范围

本轮工程收尾的核心回归为954项通过，涵盖已有训练契约及新增执行、恢复、配置和可选机制边界；真实验收最终为复习网页9/9、维护网页32/32并补验切换成功，lab原16/16被补充公式反例和文档阅读缺口阻止完整通过；详细累计成本与边界见[v0.2报告](../evaluations/v0.2-release/REPORT.md)。没有运行SFT/GRPO，也没有把本轮开发题转入训练集。Aster自行执行的正式测试、操作员独立浏览器评分和按README干净重建是不同证据，不能相互替代；尤其不能将独立浏览器通过写成Aster已支持自主浏览器验证。

以下保留原准备度审计时的专项记录，合计104项通过，不作为本轮新增计数重复累加：

```bash
/home/dzh/.conda/envs/aster/bin/python -m pytest -q \
  tests/test_rl_contract.py tests/test_protocol_pairing.py tests/test_runtime_lifecycle.py
# 100 passed

/home/dzh/.conda/envs/aster/bin/python -m pytest -q \
  tests/test_context_policy_input.py tests/test_runtime_repairs.py \
  -k 'rollout or rl or archive_failure'
# 4 passed, 12 deselected
```

这些检查验证环境、协议、终态与观察契约，不验证训练收敛或学习收益。原准备度审计没有调用模型API；后续真实工程验收包含模型调用，其产品及服务结果需另据运行记录解释。
