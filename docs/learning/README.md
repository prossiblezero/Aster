# Aster 分阶段学习文档

这是一条配合完整项目阅读的学习路径。代码按职责组织，文档按 **问题 → 机制 → 读代码 → 运行 → 验证 → 边界** 组织；无需切换课程专用分支，每个阶段都能在当前项目运行。先建立整体认识，再进入阶段 12–16 的完整实验。

## 0. 先运行，再读入口

```bash
conda activate aster
python -m pip install -e '.[dev]'
python -m pytest -q tests/test_rl_contract.py
aster --cwd examples/demo '查看仓库并说明入口'
```

前三条不调用真实模型。最后一条使用项目 `.env` 中的模型连接设置；不要把密钥复制到文档或提交到 Git。打开 [cli.py](../../src/aster/cli.py)，找到 `load_settings` 与 `Runtime.run`，就能看到命令如何进入系统。全部配置及默认值见 [配置说明](../configuration.md)。

## 1. 一次任务、一个轮次、一个步骤分别是什么？

读 [schema.py](../../src/aster/schema.py)。`RunState` 是一次运行的状态；`turn` 是模型决定下一步的次数；`step` 是工具执行次数，一轮可以发出多个工具调用。`ToolCall(name, arguments, call_id)` 是工具名称、参数与调用编号；`Final(answer)` 表示外部策略准备结束。`call_id` 把请求和结果配成一对。

`TaskSpec` 定义任务和完成条件：例如 `require_edit` 要求修改，`require_verification` 要求当前代码取得测试证据。运行 `python -m pytest -q tests/test_rl_contract.py`，观察不合法动作会在边界被拒绝。自然语言任务可以保守推断条件，正式评测应显式填写字段。

## 2. 工具为何分成工作区工具与工作流工具？

读 [Runtime._execute](../../src/aster/runtime.py) 与 [ToolRegistry](../../src/aster/tools.py)。工作区工具读取、修改文件或运行命令；工作流工具修改计划、待办、工具清单、记忆和子任务状态。两者通过一个执行入口校验参数、检查新鲜读取、判断权限、等待审批、调用 Hooks 并记账。`git_diff` 的起点基线属于运行状态，因此由 Runtime 处理。

已有文件修改前必须先读取当前内容。`Outcome` 区分 `success`、`error`、`blocked`、`timeout`、`cancelled`；`changed_paths` 记录真实变化，`partial_success` 表示发生了变化但工具未完全成功。运行 `python -m pytest -q tests/test_runtime_repairs.py -k diff_resume`，确认 todo、plan、diff 都有可运行路径。

## 3. 为什么批准命令以后还需要沙箱？

[policy.py](../../src/aster/safety/policy.py) 决定这个动作是否被允许；[workspace.py](../../src/aster/workspace.py) 限制可访问路径；[runner.py](../../src/aster/safety/runner.py) 和 [linux.py](../../src/aster/safety/linux.py) 约束子进程实际能做什么。默认把可见项目文件复制到临时目录，用 Landlock 限制文件访问、seccomp 限制系统调用，并禁止网络。

命令结束后先检查整个修改集合、写入范围和源文件是否发生并发变化，再逐文件替换。单文件替换是原子的，整个多文件回写不是数据库事务。运行 `python -m pytest -q tests/test_safety.py` 观察越界、敏感路径、网络与取消行为；安全结论以实际测试场景为范围，不把提示词当权限边界。

## 4. 如何接通不同模型接口？

[provider.py](../../src/aster/provider.py) 把 Responses、Chat Completions 与 Anthropic 请求适配到 `ModelReply`，保留调用编号、用量和协议所需的原生元数据。错误分为配置问题、连接问题、临时 HTTP 错误和请求被拒绝；只对适合重试的模型请求做有限重试。

运行 `python -m pytest -q tests/test_context_provider.py`，再读 [阶段 13](13-runtime-reliability.md) 的历史配对与重试部分。当前真实模型评测使用 GPT-5.6 Luna 的 Responses 接口；另两种协议有本地契约测试，不宣称经过真实供应商验证。

## 5. 怎样控制上下文，而不裁坏当前任务？

[ContextBuilder](../../src/aster/context.py) 先构造包含工具定义的实际协议请求，再按 `context_tokens - output_tokens` 检查输入预算。它将调用与结果成组保留，移除可丢弃的旧内容；当前任务与必需交互仍放不下时，明确停止。已采集的工具正文先脱敏归档，模型通过 `observation_read` 回读；最近完整批次共享剩余输入空间。

当前组织策略是固定的，没有上下文策略自进化。预算是本地 tokenizer 对请求的估算，远端 usage 单列。运行 `python -m pytest -q tests/test_context_provider.py -k budget`，观察正常压缩和无法压缩的请求；字段与最新裁剪顺序见 [阶段 16](16-context-archives.md)。

工具结果只裁正文，执行状态与证据编号仍保留并计入全局预算；长计划和技能可以继续翻页。[阶段 15](15-paged-observations.md) 解释 `offset`、`next_offset` 和正文预算，并验证如何读到被摘要省略的最后一页。

## 6. Hooks、停滞检测和完成条件如何合作？

[Hooks](../../src/aster/hooks.py) 在工具前、成功后、失败后和结束时提供少量插入点；前置检查异常时拒绝动作。停滞检测看是否真的有进展，成功编辑即使没有输出也不会误停。`max_recovery` 限制改变方案的机会，`max_final_revisions` 限制结束前补做的机会。

[Verifier](../../src/aster/verifier.py) 检查观察、修改、测试和必需文件；再次修改会使旧测试证据失效。运行 `python -m pytest -q tests/test_runtime_repairs.py -k 'hook or stall'`，随后看 [阶段 13](13-runtime-reliability.md) 的成功/取消收尾实验。结束钩子只运行一次，与独立任务评分是不同职责。

## 7. 如何保存中断现场？

[EvidenceStore](../../src/aster/evidence.py) 保存追加式 `trace.jsonl`、`checkpoint.json` 和 `report.json`。trace 是执行顺序记录；checkpoint 是可恢复状态；report 是结束结果。checkpoint 同时保存内容哈希与文本基线，前者检查漂移，后者还原 diff，并保存完整调用组与剩余恢复预算。

运行 `python -m pytest -q tests/test_runtime_repairs.py -k 'resume or recovery_persistence'`。工作区或有效配置漂移会阻止恢复；恢复不承诺重建已消失的进程。详细字段见 [阶段 13](13-runtime-reliability.md)。

## 8. 短期记忆、长期记忆和自进化各解决什么？

`RunState.working_memory` 整理本次任务观察，是唯一的会话工作记忆；[Memory](../../src/aster/memory.py) 保存跨运行的文件摘录，并用来源、反馈、失效原因和快照维护它们。关闭 `memory_enabled` 不会关闭工作记忆。持久化前必须引用当前运行成功的 `read_file` 证据、提供原文摘录，并通过内容与来源检查。

Dream 使用确定性规则暂停过期、被否定或反复失败的条目；回滚使用独立深拷贝，不能把已变化的文件事实恢复成真。运行 `python -m pytest -q tests/test_memory_evolution.py`，再进入 [阶段 14](14-memory-and-rollouts.md)。召回节省重复读取，与自进化选择更可靠条目的收益，要分开评测。

## 9. 用户和子任务怎样加入同一个运行过程？

[TUI](../../src/aster/tui.py) 通过后台任务显示进度，真正等待用户批准或回答；[CLI](../../src/aster/cli.py) 也有可取消的输入回调。`ask_user` 的答案作为工具结果进入后续轮次。`plan` 管执行模式，`todo` 管待办；技能通过 `skill_load` 按需读取，文本只作为不可信观察。`inspect_image` 支持工作区内常见图片，trace 不直接保存整段图片编码。

[WorkerManager](../../src/aster/workers.py) 管理受限子任务：继承父任务权限、限制递归，消息在下一轮开始时交付；完成后释放并发名额。运行 `python -m pytest -q tests/test_interaction_workers.py`，再用 `aster --tui --cwd examples/demo` 体验界面。审批、消息与取消的边界见 [阶段 13](13-runtime-reliability.md)。

## 10. 为什么现在就能准备后训练，但还没有训练算法？

[AsterEnv](../../src/aster/rl.py) 提供 `reset`、`step` 和运行轨迹。每次 reset 从种子仓库新建副本；工具与 Runtime 共用执行路径；最终奖励由宿主独立验证器检查候选代码。成功工具不会产生正分，没有独立验证器也不会给任务成功奖励。

运行 `python -m pytest -q tests/test_runtime_repairs.py -k rl_`，再读 [阶段 14](14-memory-and-rollouts.md) 的字段表和错误/正确答案实验。SFT、GRPO、训练数据划分与训练文档在真正实现后补充；现有评测题与评测轨迹保持评测用途。

## 11. 从哪里核对数量和成果？

任务定义、文件模板和评分要求放在 [datasets](../../datasets/README.md)，执行脚本放在 [scripts](../../scripts/benchmark.py)。当前结果统一从 [评测说明](../benchmark.md) 进入，学习文档不复制一份容易过期的通过数。旧报告继续保存，作为修改前对照。

```bash
python scripts/benchmark.py --task debug-and-fix
python scripts/benchmark_mechanisms.py --output artifacts/learning-mechanisms
# 下面会调用配置好的真实模型并产生费用：
python scripts/benchmark.py --real --selection real_subset.json --concurrency 2
```

[阶段 12：评测](12-evaluation.md) 解释任务、验证器与对照；[阶段 13：运行可靠性](13-runtime-reliability.md) 解释预算、恢复、Hooks 和交互；[阶段 14：记忆与训练环境](14-memory-and-rollouts.md) 解释记忆维护和奖励边界；[阶段 15：分页观察](15-paged-observations.md) 解释工具正文裁剪、完整 JSON 与长文本读取。[阶段 16：上下文归档与回读](16-context-archives.md) 配合这次重构说明全局预算、归档恢复与统一模型输入。按这个顺序运行后，再结合 [实施清单](../implementation.md) 检查自己是否能讲清每项机制的作用与限制。
