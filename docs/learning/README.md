# Aster 分阶段学习文档

这份文档是在完整 Harness 实现之后反向整理的学习路径。Aster 的代码结构不为课程拆分，但每一阶段都遵循：**问题 → 机制 → 代码 → 运行 → 验证 → 边界**。

## 0. 先运行

```bash
conda activate aster
pip install -e '.[dev]'
ASTER_API_KEY=... aster --cwd examples/demo '查看仓库并说明入口'
```

`ASTER_PROTOCOL`、`ASTER_BASE_URL`、`ASTER_MODEL`、`ASTER_API_KEY` 是 provider 配置。密钥只从环境读取，`.env` 已被 `.gitignore` 忽略。`sandbox` 有 `required / best_effort / off`；默认 required，隔离后端不可用会拒绝执行而不是静默裸跑。

## 1. 数据契约：Run、Turn、Step 和 ToolCall

文件：[schema.py](../../src/aster/schema.py)。模型输出不是直接执行的字符串，而是 `ToolCall(name, arguments, call_id)` 或 `Final(answer)`。`RunState` 保存任务、轮次、步骤、验证状态、修改路径和反馈。`call_id` 用来把模型请求、工具结果、trace 和 RL `info` 关联起来。

`TaskSpec.require_edit / require_verification` 是完成契约；交互任务可由 `verifier.infer` 做保守推断，训练评测应直接传结构化字段。

## 2. 唯一 ToolExecutor 与安全闸门

文件：[runtime.py](../../src/aster/runtime.py)、[tools.py](../../src/aster/tools.py)、[policy.py](../../src/aster/safety/policy.py)。每个动作按固定顺序经过 schema、fresh-read、deny-first 权限、审批、hook、工具执行和结果分类。`permission=never` 是“无人工审批”，不是“无条件允许”。已有文件修改前必须有当前内容的读取指纹，防止模型依据过期内容 patch。

工具结果区分 `success / error / blocked / timeout / cancelled`；partial success 会保留工作区变化，不能被当成完全失败。

## 3. 工作区边界和进程隔离

文件：[workspace.py](../../src/aster/workspace.py)、[runner.py](../../src/aster/safety/runner.py)、[linux.py](../../src/aster/safety/linux.py)。路径先 canonicalize，再检查工作区、symlink、敏感文件和 `.git/.aster/.agents` 等保护目录。

shell 在临时副本中运行；Linux 后端使用 Landlock 文件权限、seccomp 系统调用过滤、环境白名单、资源上限和进程组终止，成功后按完整写集原子回写。这样 Agent 即使被 README 中的伪指令欺骗，也不能获得新的文件或网络权限。

## 4. Provider 与 Tool Calling

文件：[provider.py](../../src/aster/provider.py)。Responses、Chat Completions 和 Anthropic-compatible 三种协议都转换为 `ModelReply`。模型返回 tool call 后，Aster 执行工具，再用带 `call_id` 的 function-call-output 回填。安全 system prompt 和 `[untrusted tool observation]` 标签由 [context.py](../../src/aster/context.py) 每轮重建，所以 compact 后不会丢失安全边界。

## 5. Context Engineering

`ContextBuilder` 为 system、task、memory、skills、history 分配预算，预留输出 token；超预算时优先移除旧的普通观察，保留失败、修改和最近消息。`ContextReport` 记录估算 token、预算、移除数量和各 section 大小。当前上下文策略是固定的，**没有上下文策略自进化**。

v2 压力评测发现当前预算使用中间文本估算，未覆盖最终工具定义；完整输入预算检查尚未通过。不要据此认为任意长历史都可控。

token 是估算值：优先使用 `cl100k_base`，没有 tokenizer 时退化为字符估算；真实供应商 usage 仍以 provider 返回值为准。

## 6. Hooks、停滞和完成治理

文件：[hooks.py](../../src/aster/hooks.py)、[recovery.py](../../src/aster/recovery.py)、[verifier.py](../../src/aster/verifier.py)。`Hooks` 接收可注入插件，按 before、成功、失败、停止四个时机运行；插件异常会被隔离，不能扩大权限，before 插件可以拒绝或停止动作。Nexa 的重复动作、ABAB 振荡和相同输出检测被实现为有界 `StallDetector`，最多一次 recovery nudge，继续无进展就停止。

最终文本先经过 completion contract 和 verifier。`测试通过 → 再修改` 会将验证状态重置为 `missing`，不能把旧测试结果继续当证据。

## 7. Checkpoint、Trace 和 Resume

文件：[evidence.py](../../src/aster/evidence.py)。每个 run 生成追加式 hash-linked `trace.jsonl`、checkpoint 和 report。checkpoint 包含工作区 hash、运行边界、状态消息、结构化 provider 历史、验证状态和下一步状态。resume 会重新检查 runtime identity 与工作区 hash；漂移就拒绝恢复。trace 对密钥、token 和图片做脱敏/省略。

## 8. Layered Memory 与 Memory Evolution

文件：[memory.py](../../src/aster/memory.py)。每次运行的 `RunState.working_memory` 保存当前任务读过的文件、搜索结果和测试结果摘要；下一轮由 `ContextBuilder` 注入，因此它是会话内的短期工作记忆。文件发生修改时对应摘要会被删除，要求重新读取，避免继续使用旧事实。跨运行经验进入 durable lessons，按关键词召回；evolution ledger 保存 support、contradictions、use feedback、provenance 和 snapshot。没有 `run_id + evidence_id` 的 lesson 会被拒绝；Dream 目前是确定性去重，模型辅助冲突整理留到后续迭代。

v2 负例显示，持久记忆还会接纳有证据的测试敏感内容和临时目标；来源检查不等于内容适存性检查。

记忆被召回后，后续验证成功或失败会记录使用反馈，但这不等于证明单条记忆产生了因果收益。

## 9. Skills、Plan、Todo、Workers 和多模态

skills 只从工作区目录按需加载，skill 文本标记为不可信；plan 模式阻止写文件；todo、plan、worker 使用同一 Runtime。Explore worker 是只读、有限步数的子运行；写 worker 必须有明确 `write_scope`。`inspect_image` 只读取工作区内的 PNG/JPEG/WEBP/GIF，图片不直接写入 trace。

## 10. Agent-RL 接口

文件：[rl.py](../../src/aster/rl.py)。`AsterEnv.reset(task)` 返回 observation/info，`step(action)` 返回 `observation, reward, terminated, truncated, info`。action 与生产 Runtime 使用同一个 `ToolCall` 或 `Final`，所以 RL 不会绕过 permission、sandbox、fresh-read、verification 或 completion gate。

这一层只提供环境和轨迹契约，不包含 SFT、GRPO、advantage 或训练脚本。后训练文档等 Aster 评测完成后另行编写。

## 11. 评测阅读方法

先读 [从一道 bug 修复题读懂评测](12-evaluation.md)，配合 [任务数据](../../datasets/benchmark.md) 与 [benchmark.py](../../scripts/benchmark.py) 实际跑一遍。

```bash
conda activate aster
python -m pytest -q
python scripts/benchmark.py --task debug-and-fix
python scripts/benchmark.py --real --selection real_subset.json --concurrency 2
```

当前报告位于 `artifacts/benchmark-v2-scripted/` 和 `artifacts/benchmark-v2-real/`。参考动作回放、真实模型表现、模块开关对照分开统计；含义及未通过项见 [评测说明](../benchmark.md)。v1 报告保留作历史记录，不再引用其中旧量化指标。
