# 14. 让记忆可以纠错，让训练环境不能靠自报结果拿奖励

本阶段把两个边界连起来：Agent 应从哪些证据中形成跨运行记忆；后续训练又应凭什么判断一次尝试做对了。先看 [memory.py](../../src/aster/memory.py)、[Runtime 的记忆工具](../../src/aster/runtime.py) 和 [rl.py](../../src/aster/rl.py)。这里已经实现的是 Harness 与环境接口，SFT、GRPO 算法和训练数据流水线尚未实现。

## 问题：有聊天历史，为什么还要 working memory？

完整历史按时间记录“发生过什么”；工作记忆整理“接下来可能仍要用哪些事实”。`RunState.working_memory` 用文件路径作为键，保存刚读过的文件、搜索和测试摘要。例如读过 `config.py` 后，下一轮可以直接知道它的大致内容，不必为了回忆路径和配置再读一次。

每条工作记忆中的 `summary` 是最多 500 个字符的短摘要，`updated` 是更新时间。它只有 `RunState` 这一处所有者，随 checkpoint 保存；修改文件后删除对应摘要，文件变更也使搜索和测试摘要失效。`ContextBuilder` 会尝试注入这些摘要，但输入紧张时可以让出预算。因此“仍存在状态中”和“本轮一定送给模型”是两件事。

`memory_enabled=false` 只关闭跨运行记忆。普通任务与 Worker 仍保留工作记忆；Worker 不更新共享持久记忆，避免多个运行覆盖同一个记忆文件。

```bash
conda activate aster
python -m pytest -q tests/test_memory_evolution.py -k disabled_durable
python -m pytest -q tests/test_fresh_read.py
```

读测试中的 `working` 字典，再看 Runtime 的 `_refresh_observations()`：工具完成后和下一轮构建输入前，会复查已读文件的文本哈希。子任务或外部编辑改了文件，即使父任务以前已经改过同一路径，也会清除旧摘要和读取凭据；搜索、测试摘要及旧验证一并失效。工作记忆中的摘要不能代替编辑前的读取指纹：摘要可能截断，`read_evidence[path]` 则记录完整文件的内容哈希，用于判断编辑依据是否仍新鲜。

## 问题：有 evidence_id，就能保存任何长期经验吗？

不能。模型可以引用一个真实事件，却写下一条与事件无关的话。[Runtime.workflow_tool](../../src/aster/runtime.py) 先确认 `evidence_id` 对应**当前运行成功的 `read_file` 结果**；结果必须带源文件路径和完整内容哈希。然后 `Memory.record` 要求保存的文字是观察内容中的摘录，检查来源仍未变化，并拒绝能识别的敏感内容、临时目标和越权指令。

例如文件记载“Cache TTL is 60 seconds.”，这个原文摘录可以成为候选记忆；“以后永远不用检查缓存配置”是额外推断，不能凭这次读取直接入库。`memory_retrieve` 返回的旧文字也不能反过来当作新的证明，否则过期记忆可以反复引用自己，洗掉原文件来源。

沿着 `memory_store` → `Memory.record` → `Memory.retrieve` 阅读，认识下面这些字段：

| 字段 | 含义 |
|---|---|
| `id` | 摘录文本的稳定标识，用于更新、反馈和删除 |
| `text` / `tags` | 原文摘录 / 召回用的标签 |
| `provenance` | 来源记录，含 `run_id`、`evidence_id`、证据类型与时间 |
| `sources` | 源文件路径到内容哈希的映射；文件变更、删除或变成符号链接后不再召回 |
| `support` | 不同来源事件提供的支持计数；不是“这句话正确的百分比” |
| `contradictions` | 被明确否定的次数；有否定就不进入自动召回 |
| `retrievals` | 被召回的次数，只是使用观测 |
| `successful_uses` / `failed_uses` | 召回后验证成功 / 失败的反馈计数 |
| `inactive_reason` | 暂停自动召回的原因，例如 `stale_source`（来源过期）、`repeated_failure`（反复失败） |

```bash
python -m pytest -q tests/test_memory_evolution.py
python -m pytest -q tests/test_runtime_repairs.py -k memory_cannot_launder
```

准入数据位于 [memory_evolution_cases.json](../../datasets/memory_evolution_cases.json)。先看 `reject_` 开头的场景，再看 `fresh_source_modified`：来源是真实的，只要已经改变，原记忆就不能继续作为当前事实使用。

边界：当前准入刻意保守，只支持可追溯的文件摘录，不自动提炼任意成功工具结果，也不自动归纳语义经验。敏感信息规则是有限检测，来源检查证明“确实读到过”，不能证明源文档本身永远正确。

## 问题：记忆自进化到底改变了什么？

它改变召回排序和条目的可用状态，不自动修改上下文组织策略。当前排序先计算查询词与条目的匹配，再结合支持记录及成功/失败反馈。多次用过且验证成功的条目在相近匹配条件下更靠前；无关查询仍不会因为条目热门就被召回。

`dream()` 是一个确定性的维护函数。它标记来源过期、被否定或反复失败的条目，保留原记录供检查和回滚。反复失败的当前阈值是 `failed_uses >= max(2, successful_uses + 2)`；一次失败不会立即废弃经验。`auto_dream=true` 在一次运行结束时自动执行一次；`memory_evolution=false` 关闭使用反馈和这次维护，但安全准入、显式删除和来源新鲜度检查仍然有效。

`Runtime` 在 `run_tests` 成功或报告 `tests_failed`（测试主体失败）时，给已召回的条目记录一次反馈并清空待反馈标识。手动召回与下一轮自动召回的标识取并集，避免自动查询没有命中时覆盖刚使用的记忆。可识别的采集或初始化错误、空测试、工具拒绝和超时不会否定记忆。测试主体内部抛出的异常可能仍被框架记为 failure，当前机制不会从异常反推是哪条记忆造成的；这是保守的使用反馈，不是因果归因。

```bash
python scripts/benchmark_mechanisms.py --output artifacts/learning-mechanisms
```

打开生成的 `report.md` 和 `report.json`，按 `evolution_off` / `evolution_on` 看同一组反馈场景。`contract_passed` 表示该配置是否符合其声明行为；`goal_passed` 表示是否达到两组共用的目标。关闭机制的对照可以合同正确，但达不到目标，不能把两个概念混成同一个通过率。

边界：这些固定反馈场景验证排序与维护是否生效，成功/失败反馈由任务预先给定。它们不能直接证明真实长任务中哪一条记忆造成了成功。真实记忆召回节省工具调用的收益，也不能全部记到“自进化”头上；当前量化结论见 [评测说明](../benchmark.md)。

## 问题：回滚之后再运行，为什么不能改坏旧快照？

如果 `lessons = snapshot["lessons"]` 只是让两个变量指向同一份列表，后续增加反馈时就会连快照一起改掉。[Memory.snapshot / rollback](../../src/aster/memory.py) 在保存和恢复两侧都做深拷贝，使快照保持独立。快照最多保留 8 份，条目最多保留 64 条，审计记录最多保留 256 条，避免轻量项目无限积累状态。

回滚恢复的是条目及计数，不回滚项目文件。即使恢复出一条旧配置，召回时仍要检查文件哈希；文件已经变了，旧事实依然不可用。数据中的 `rollback_snapshot_is_immutable` 与 `rollback_cannot_restore_stale_truth` 分别检查这两个条件。

## 问题：后训练时，Agent 能否靠多调几次工具拿高分？

[AsterEnv](../../src/aster/rl.py) 每次 `reset(task)` 都从种子仓库创建临时副本，过滤运行控制文件，因此上一轮修改和跨运行记忆不会带进新 episode。这里的 episode 就是一次完整尝试。环境的 `step(action)` 调用同一个 Runtime 执行工具与完成判断；训练策略无法通过这条接口跳过审批、沙箱或编辑前读取。

为了避免重复工具刷正分，当前只有最终任务结果能产生正奖励。读文件成功、写文件成功只是过程证据。仓库自带测试可能被 Agent 修改，因此最终奖励由宿主传入的独立 `verifier(root, state)` 决定：`root` 是当前尝试的候选仓库路径，`state` 是运行状态的深拷贝；返回 `VerifierResult(passed, reason, details)`。验证器可以是同步或异步函数，校验前先停止并等待 Worker，固定候选状态。

| 返回值或字段 | 含义 |
|---|---|
| `observation` | 当前任务、消息、工作记忆、工具定义、修改路径和验证状态；供外部策略决定下一步 |
| `reward` | 中间成功动作 `-0.05 / max_turns`，失败动作或被要求补做的 Final 为 `-0.5 / max_turns`；终态独立验证通过 `+1`，失败 `-1`，无独立验证器的合规完成 `0` |
| `terminated` | 环境正常完成或运行失败，当前尝试结束 |
| `truncated` | 因步骤、轮数或外部停止而截断；完成条件或停滞恢复耗尽属于 `terminated` |
| `info.verification` | Agent 在工作区内取得的测试证据 |
| `info.reward_verification` | 宿主独立验证器给出的最终判定；与上一个字段分开保存 |
| `info.completion_verification` | Harness 对必需编辑、观察、测试与产物的完成条件检查；外部评分通过也不能绕过必需条件 |
| `seed` | `reset` 返回的实验标识；当前不设置远程模型随机性，不保证模型结果可复现 |

终态奖励替代当步成本，预算耗尽也给最后候选评分，所以最后一次工具刚好完成任务也不会漏奖。每次决策消耗一轮，中间成本总和小于 `0.5`：在未折扣的 episode 回报下，成功总回报大于 `0.5`，失败不高于 `-1`，拖延后失败更低。这样既不奖励无意义工具，也不会因为长任务成本累加而鼓励立即认输。未来训练若引入折扣，需要重新检查这个激励关系。

没有独立验证器时，正常结束奖励为 `0`，失败或截断为 `-1`。结束后再 `step` 返回 `episode_finished`；一次动作尚未结束就并发 `step/reset/close` 返回 `episode_busy`，防止重复评分。Final 和预算终态会取消子任务并等待它们退出后再评分，并非等待子任务自然完成。Verifier 出错保留 `verifier_error:<异常类型>`，训练采样器应区分评分环境错误和任务答案错误。

```bash
python -m pytest -q tests/test_rl_contract.py
python -m pytest -q tests/test_runtime_repairs.py -k rl_
python -m pytest -q tests/test_review_regressions.py -k rl_
```

最值得逐行运行的是 `test_rl_isolated_reset_sparse_reward_and_trusted_verifier`：同一题先交错误答案，再 reset 后交正确答案。观察原始种子文件没有改变、下一轮恢复初始内容、最终奖励只由独立检查决定。之后读 `AsterEnv._observation`，就能看到模型需要什么输入、采样器将来需要记录哪些字段。

边界：验证器是宿主信任的代码，调用者负责把隐藏测试与候选仓库隔离。环境本身不会把一个任意回调自动变成安全判分器；项目 benchmark 已提供候选副本与独立测试的执行方式。`close()` 清理临时工作区；如果仍有活动 Worker，应先 `await env.runtime.workers.close()`。后续 SFT/GRPO 需要另外设计训练划分、采样、优势估计与优化器；本阶段不包含这些训练实现，也不能把现有评测集直接当训练集。
