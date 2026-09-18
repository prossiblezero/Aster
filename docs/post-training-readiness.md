# 后训练准备度审计

审查日期：2026-09-18。依据当前源码、数据集约定和现有回归测试；本文件是方案设计依据，不表示已实现训练算法。

**Aster 已有足够基础开始数据集、SFT 和 GRPO 的方案设计，也能支持小规模外部策略 rollout；目前还不能直接作为完整训练系统开训。** 下一步应连接训练数据与策略接口，无须先照其他 Agent 重写核心。

2026-09-18 实施补充：[后训练方案与数据契约](post-training-design.md) 已补齐可执行的任务隔离、实际请求到 SFT 投影、assistant-only mask、GRPO 版本/概率/分组/预算校验。新模型回复绑定实际请求摘要，旧缺少绑定的证据仅作审计，不能直接导出。尚未采集新训练集、接入真实 tokenizer 或启动训练；下文原审计中“尚无接口”的描述以此更新为准。

## 已有基础

| 能力 | 当前已实现的行为 | 源码与检查入口 |
| --- | --- | --- |
| 独立 episode | `AsterEnv.reset()` 按工作区规则复制种子仓库到临时目录；上一轮改动不会变成下一轮起点 | [rl.py：AsterEnv.reset](../src/aster/rl.py)、[test_runtime_repairs.py](../tests/test_runtime_repairs.py) |
| 统一执行与观察 | 外部动作复用 `Runtime.step()`；`model_input` 与工具定义来自自主运行同样使用的 `prepare_request()` | [rl.py：_step / _observation](../src/aster/rl.py)、[runtime.py：prepare_request](../src/aster/runtime.py)、[test_context_policy_input.py](../tests/test_context_policy_input.py) |
| 有界交互与终态 | 工具动作和 Final 消耗预算；达到上限仍评分最后候选；终态拒绝重复 step，评分前等待 Worker 停止 | [rl.py：_step / _terminal_reward](../src/aster/rl.py)、[test_rl_contract.py](../tests/test_rl_contract.py) |
| 独立奖励入口 | 完成要求检查与外部 verifier 分开；没有独立 verifier 不给成功奖励，仓库测试结果只是观察 | [rl.py：_terminal_reward](../src/aster/rl.py)、[schema.py：VerifierResult](../src/aster/schema.py) |
| 轨迹与输入证据 | 保存实际请求 payload、上下文策略版本、工具结果归档、调用历史、checkpoint 和代码差异；检查工具调用与结果配对 | [runtime.py：prepare_request / checkpoint](../src/aster/runtime.py)、[provider.py：history_groups](../src/aster/provider.py)、[evidence.py](../src/aster/evidence.py) |
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

当前请求工件中的 `policy_version` 来自 `ContextReport`，表示上下文构建规则，当前为 `deterministic-v5`；它不是行为模型的权重版本。模型名称也不能替代不可变的训练检查点标识。相关契约见 [schema.py：ModelReply](../src/aster/schema.py)、[provider.py：ModelClient](../src/aster/provider.py)、[context.py：ContextReport](../src/aster/context.py)。

### 4. 明确奖励可信范围与异常处理

现有奖励入口已有良好约束：独立 verifier、完成要求校验、预算到期评分、终态保护和评分前停止 Worker。当前中间动作代价按 `max_turns` 归一化，终局采用 `+1/0/-1`；这是可讨论的初始奖励契约，不是已经验证有效的训练目标。

隐藏评分将候选复制后再加入固定测试，关闭候选 `conftest.py` 和第三方 pytest 插件自动加载，并核验结构化报告。但候选 Python 与 pytest 仍处于同一解释器，不能宣称对主动伪造评分的策略完全抗作弊。当前边界适合可信任务集上的小规模试验；若训练策略会主动探索评分漏洞，需要进一步隔离候选执行进程与可信断言进程。

训练方案还应区分候选错误、基础设施超时和 verifier 自身故障，记录奖励版本并检查异常高分轨迹。不能把模型自报完成、公开测试通过或 stdout 中的 `passed` 当成唯一奖励。依据见 [benchmark_support.py：external_check](../scripts/benchmark_support.py) 和 [grader-boundary.md](grader-boundary.md)。

### 5. 统一动作、随机性与整个任务的预算

外部 `AsterEnv.step()` 每个 ToolCall 或 Final 算一次决策轮次；自主 Runtime 的一次模型回复则可能包含多个工具调用。首版训练需要明确选择单调用动作契约，或另行定义批次适配，不能直接把两者的轮数当成相同单位。

`reset(seed=...)` 目前只把 seed 返回到 info，没有设置模型采样或依赖库的随机状态。可复现实验仍需训练器控制采样种子，固定任务、模型、工具与上下文版本，并记录环境差异。

Worker 当前各有 12 步、12 轮预算，`max_workers` 限制同时存活数量；没有整个父子任务共享的 token、耗时和动作成本上限。首版 SFT/GRPO 可以关闭 Worker，先验证单 Agent 学习闭环。需要训练委派策略时，再加入整个任务的预算、父子轨迹关联和奖励归因，不能只统计父运行的调用成本。依据见 [rl.py：reset / _step](../src/aster/rl.py)、[workers.py：WorkerManager.start](../src/aster/workers.py)。

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

## 本次审查的验证记录

使用 `/home/dzh/.conda/envs/aster/bin/python` 运行以下现有检查，合计 **104 项通过**：

```bash
/home/dzh/.conda/envs/aster/bin/python -m pytest -q \
  tests/test_rl_contract.py tests/test_protocol_pairing.py tests/test_runtime_lifecycle.py
# 100 passed

/home/dzh/.conda/envs/aster/bin/python -m pytest -q \
  tests/test_context_policy_input.py tests/test_runtime_repairs.py \
  -k 'rollout or rl or archive_failure'
# 4 passed, 12 deselected
```

这些检查验证环境、协议、终态与观察契约，不验证训练收敛或学习收益。本次审计未运行 SFT/GRPO，也未调用模型 API。
