# 12. 从一道 bug 修复题读懂评测

这章按“问题 → 代码 → 运行 → 证据”的顺序阅读，不要求先懂整个 Harness。

## 第一步：为什么不能只听模型说完成

打开 [debug-and-fix 的初始仓库](../../datasets/fixtures/long_session_debug_fix/)。任务要求修复行为，模型的最后一句话不能证明函数变正确。因此我们提前写好 [独立验证器](../../datasets/checks/debug-and-fix.py)：`test_fix` 检查新行为，`test_regression` 检查必须保留的旧行为。验证器在 Agent 结束后才加入副本。

在 [任务清单](../../datasets/benchmark_tasks.json) 中搜索 `debug-and-fix`。`prompt` 是要求，`fixture` 是初始目录，`checks.verifier` 是评分代码，`actions` 是用于检查 Harness 的参考调用。真实模式只传 prompt 和 fixture，不读取参考动作来替模型回答。

## 第二步：让评分标准先经得起检查

```bash
conda activate aster
python scripts/benchmark.py --task debug-and-fix
```

阅读 [benchmark.py](../../scripts/benchmark.py) 的 `baseline_check`：先运行原始版本。目标测试应该失败，原有行为应该通过。再看 [benchmark_support.py](../../scripts/benchmark_support.py) 的 `run_task`：新建工作区，启动同一套 Runtime，执行参考动作，最后交给 `external_check`。

若 reference 失败，先看测试输出：`assert` 失败是行为不符，`ModuleNotFoundError` 是依赖或导入环境错误，`no tests ran` 是验证器无效。不能把这几类失败都叫成“模型不会做”。

## 第三步：把参考动作换成模型

```bash
python scripts/benchmark.py --real --task debug-and-fix --repeats 1
```

`ScriptedPolicy` 按预先写好的动作走，用于检查运行边界。`--real` 换成配置好的模型，由它自己决定读哪些文件、怎样改、何时测试。两者走相同 Runtime；前者通过不意味着后者通过。

这题分别跑 core 和 all 两种工具集合。报告中的 `steps` 是工具调用次数，`turns` 是模型请求次数，`passed` 是所有评分条件都成立，`stop_reason` 是为何结束。某次可能外部测试已通过，却因 Runtime 停滞判断提前结束；这种差异需要读 trace，不能只留成功那一列。

## 第四步：用记忆题理解对照

```bash
python scripts/benchmark.py --task memory_deploy
python scripts/benchmark.py --real --task memory_deploy
```

看 `run_memory`：两组都先读文件并存储证据，再创建新的 Runtime 回答同一个问题。关闭组能重新读原文件；开启组可以召回。`fresh_followup` 检查后续运行没有继承原始聊天和会话摘要，`acquisition_steps` 单独记录学习成本。

这只回答“已有事实能否帮助后续少读文件”，没有证明系统会自行发现优质经验，更没有证明自进化优于固定记忆。不同问题必须另外设计数据集。

## 第五步：主动找反例

读 [test_benchmark.py](../../tests/test_benchmark.py)：故意清空公开测试、注入 pytest 配置、关闭已有记忆、给策略一个不同于题库答案的观察。评测器应当识别错误或使用真实观察。

再运行全量 benchmark。出现红色结果就查看 `checks`、trace 和独立测试输出。v2 曾发现完整输入超预算、持久记忆接纳不合适内容；v3 修复后重跑相同任务，同时保留旧失败记录。评测的作用是找到并解释变化。

新增任务按 [数据集字段说明](../../datasets/README.md) 操作。任务内容放 datasets，脚本只负责执行和判分；具体实验结果在 [评测说明](../benchmark.md) 中查看。

## 第六步：把模块效果与任务能力分开

```bash
python scripts/benchmark_mechanisms.py
```

打开 [记忆场景](../../datasets/memory_evolution_cases.json) 和 [运行场景](../../datasets/runtime_cases.json)。`variants` 指实验条件，例如机制开启/关闭或不同恢复额度，`expected` 指每个条件本来应该出现什么行为，`goal` 指两组共同想达到的目标。比如关闭恢复时按约定停止，属于契约通过，但没有完成后续修复，所以目标未达成。

[机制报告](../../artifacts/benchmark-v4-mechanisms/report.md) 的两列正是这两个含义。反馈场景把成功/失败标签作为固定输入，检验这些反馈是否改变记忆选择。它证明代码机制生效，不能替代真实模型上的端到端收益实验。

记忆专项包含 12 个准入、4 个来源变化、6 个反馈调整、2 个回滚场景，各运行开启/关闭两种条件。6 个反馈场景包含四个应改善召回的案例和两个不应误删好记忆的控制案例，共同目标由关闭组 2/6 提升至开启组 6/6。可以单独运行 `python -m pytest -q tests/test_memory_evolution.py`。真实任务的“开启记忆更省工具调用”属于召回收益，不能代替“开启演化比固定记忆更好”的实验。

工具分组也需要反例。运行 `python -m pytest -q tests/test_tool_contracts.py -k disabled_capabilities`，检查 [16 组配置](../../datasets/tool_visibility_cases.json) 下的 core/all 定义、加载目录和直接调用。例如禁用 Worker 后，启动、查询、指导和停止工具都应消失；仅关闭记忆演化时，保存和召回工具应保留。工具的完整参数定义是否真的在加载后才出现，由 `test_core_catalog_loads_optional_schema_then_executes_tool` 通过连续 Runtime 轮次检查。

## 第七步：上下文装得下，任务就一定做得对吗

先打开 [上下文与恢复任务手册](../../datasets/robustness.md)，再看 [上下文任务清单](../../datasets/context_task_cases.json)。原先的静态压力检查只把构造的历史交给 `ContextBuilder`，检查输入预算、当前请求和指定文字是否保留。想象你把一份故障排查记录缩成三页：页数达标，并不代表排查所需的端口、单位和用户约束都还在。任务能力需要由模型继续修改代码，再用隐藏测试检验。

选择 `context_retry_delay` 阅读：早期工具观察包含重试规则，同样的规则也能从仓库 `SPEC.md` 补读。再看 `context_decimal_rounding`：舍入约束只出现在用户历史里，不能靠重新读实现猜出要求。其他题检查跨文件一致性，以及用户后续纠正旧决定时是否采用最终版本。额外的 `context_ephemeral_route` 是信息丢失控制：答案只存在于旧工具观察，没有文件副本；它用来暴露裁剪的能力边界，单独统计，不混进十二道普通任务的成绩。

接下来打开 [benchmark_robustness.py](../../scripts/benchmark_robustness.py) 的 `initialize`。它为每次尝试复制独立工作区，先加入题目 `history`，再加入 [共同长历史](../../datasets/context_history.json)。三个条件使用相同内容和顺序，只改变 [实验协议](../../datasets/robustness_protocol.json) 中的 `context_tokens`：

| 条件 | `context_tokens`：总窗口 | `output_tokens`：输出预留 | 实际输入上限 |
|---|---:|---:|---:|
| `full` | 32,768 | 2,048 | 30,720 |
| `balanced` | 8,192 | 2,048 | 6,144 |
| `tight` | 5,632 | 2,048 | 3,584 |

实际输入上限等于 `context_tokens - output_tokens`，其中还要容纳系统提示、工具定义和当前请求，并非全部留给历史。`tool_output_tokens` 是单份工具正文的上限，也不是整个输入上限。中间档用于观察更实用的预算折中；这三个固定配置不能证明某个数值对所有仓库都最优。

顺着 `initialize` 的 `preview` 走到 [context.py](../../src/aster/context.py) 的 `ContextBuilder.build`：预览使用状态副本，正式运行仍由生产策略处理历史。用户消息保留，可裁剪的旧工具观察让出空间，结构化工具调用与结果保持配对。因此早期文件证据可能需要补读，而仅存在于用户历史的要求应该仍可见。预算太紧时，连最近完整的工具交换都装不下，运行也可能报预算错误；初始请求能发送不等于整个任务都能发送。

再读 `ObservedModel.complete`。它包在真实模型外面，先把实际请求归档，再原样转发。`history_evidence` 只是待查找的历史原文列表，用来测量这些内容有没有进入请求；执行器不会重新注入这些文字，也不会给它们加 `important` 保留标记。否则相当于考试时把删掉的答案重新递给模型，无法检验真正的裁剪效果。

先做不调用模型的预检，再对同一道题运行三档预算各一次：

```bash
conda activate aster
python scripts/benchmark_robustness.py --suite context --task context_retry_delay --output artifacts/learning-context-preflight
python scripts/benchmark_robustness.py --real --suite context --task context_retry_delay --repeats 1 --concurrency 1 --output artifacts/learning-context
```

第一条检查原始目标失败、原有行为通过、参考修复可通过，以及三档初始输入是否满足预设裁剪条件。第二条才回答模型在不同预算下能否完成任务。打开 `artifacts/learning-context/report.json` 的 `rows`，沿每条 `trace_path` 找到执行记录；同次尝试目录下的 `requests/request-001.json` 是首次实际请求，`candidate` 是最终代码，`patch.diff` 是改动，`result.json` 包含隐藏测试结果。

阅读时重点区分以下三个字段：

| 字段 | 它实际测量什么 | 不能直接推导什么 |
|---|---|---|
| `first_request` | 首次请求的本地估算 token、请求哈希、`evidence_present` 等；没有发出请求时为 `null` | 首次输入短，不等于整个任务总成本低；文字在场，不等于模型正确使用 |
| `history_compactions` | 正式上下文构建中，实际删除过历史消息的次数 | 不统计所有正文截断，也不是删除消息总数；后者看 `removed_messages` |
| `evidence_read_calls` | 接手后成功调用 `read_file` 读取 `evidence_paths` 指定来源的次数 | 为零不代表没有通过 `search` 或 shell 获取同样内容 |

一个有意义的结论需要把这些列连起来：紧预算确实删过历史，模型是否补读来源，最终是否仍通过隐藏测试，以及代价如何变化。分别报告这四件事，不能用“全部满足预算”替代任务完成率。

## 第八步：运行时发现停滞后，模型会不会真正换一种做法

打开 [恢复任务清单](../../datasets/recovery_task_cases.json)，选择 `repeat_debug-and-fix`。`base_task` 引用现有代码题，`seed_actions` 用真实工具制造重复读取，`expected_trigger` 声明应该触发的停滞原因。十二个场景覆盖重复读取、交替振荡、重复错误和相同失败输出，但它们复用了五道代码题；三个故障场景或三次重复都不能算成三道新增代码题。

回到 `initialize`：脚本先让 `ScriptedPolicy` 只执行故障前缀，并检查检测器真的触发。接着看 `run_attempt` 的 `benchmark_handoff`：这里记录交接位置，随后把 `runtime.model` 换成包着真实模型的 `ObservedModel`。从这一步开始，读什么文件、改哪里、是否测试均由模型选择；参考修复不会继续替它执行。这样才能验证“运行时的干预能否帮助模型脱困”。

主对照是 `feedback` 与 `silent`：两组 `max_recovery` 都为 2，前缀均消耗一次，交接后都剩一次。`feedback` 使用生产机制注入纠偏提示；`SilentRecovery.add_message` 只拦截那条恢复消息，仍保留检测、计数、清理检测窗口、安全检查与其他反馈。这不是完全不给模型看错误：普通工具结果仍然存在，实验只移除 Harness 额外指出停滞的那条消息。

`stop` 的恢复额度为 0，用来检查停止边界，前缀触发后就结束，不再调用模型。它无法继续是条件本身决定的，因此不能把 `stop → feedback` 的完成差异写成纠偏提示收益。提示是否有帮助，要比较同样允许继续的 `silent → feedback`；两者相同或者静默组更好，也应保留结果。

```bash
python scripts/benchmark_robustness.py --suite recovery --task repeat_debug-and-fix --output artifacts/learning-recovery-preflight
python scripts/benchmark_robustness.py --real --suite recovery --task repeat_debug-and-fix --repeats 1 --concurrency 1 --output artifacts/learning-recovery
python -m pytest -q tests/test_robustness_benchmark.py tests/test_recovery_contracts.py
```

真实命令让两种继续条件各运行一次，并保留一次停止参照。回归测试则检查实验有没有只改变约定因素：`test_silent_continuation_suppresses_only_feedback_and_preserves_seed` 比较前缀后的状态；`test_request_observer_forwards_unchanged_and_matches_escaped_evidence` 检查记录器没有偷偷改请求。它们检验实验可靠性，不能替代真实任务成绩。

在恢复报告里，`first_call_changed` 表示首次自主调用与故障前缀中的调用不同；参数会补齐默认值、规范化路径，`a.py` 改写为 `./a.py` 不算换动作。这是评测记录器的归一化，不代表生产停滞检测器已经识别所有等价参数。它只是行为观察：换成另一个仍然失败的工具，也会显示改变。`renewed_stalls` 是交接后再次检测到停滞的次数，不包含预置前缀的那一次。最后仍要看 `passed`：运行合同与隐藏测试共同通过，才算完成。

`autonomous_steps` 和 `autonomous_turns` 分别只计交接后的工具调用和模型轮次，避免把人为制造故障的步骤算成模型解题效率。故障前缀保证每个条件遇到相同问题，但它不是自然发生的长任务记录，所以本实验也不测自然任务里重复读取究竟有多常见。

## 第九步：先看成功与失败，再比较成本

最后读 `run_attempt → summarize → paired_summary`。`run_attempt` 归档每次尝试并判分，`summarize` 按专项、类别和条件保留所有尝试，包括连接错误和预算失败；`paired_summary` 再按同一题目、同一重复序号配对。序号对应一次预先安排的重复，不意味着两次模型采样完全相同。

报告中的 `both_pass` 是两边都完成，`left_only`、`right_only` 是只有一边完成，`neither` 是两边都失败。这四项相加应等于 `pairs`。例如“上下文 / 文件证据 / full / tight”中，左边是充足预算，右边是紧预算；恢复配对则固定为左侧 `silent`、右侧 `feedback`。先看单组完整完成率及这些胜负关系，再讨论 token。

重复三次还可以计算 Pass@1 和 Pass@3。假设某题三次中成功两次：从这三次样本估计的 Pass@1 是 `2/3`，Pass@3 是 `1`，因为三次中至少一次成功。对每题分别计算后再平均，不能把“12 题 × 3 次＝36 次”直接叫作 Pass@3。正式批次中，充足／中等预算的 Pass@1 为 97.22%／75.00%，Pass@3 都为 100%；后者相同并不代表单次可靠性相同，也不代表无需隐藏评分就能选出正确答案。复算复用 [benchmark.py](../../scripts/benchmark.py) 的 `pass_at_k`，逐题计数见 [派生指标](../../artifacts/benchmark-v5-robustness/derived-metrics.json)。

`common_success_total_tokens` 只比较双方都完成的同题配对，数组顺序为 `[左侧平均值, 右侧平均值]`；没有共同成功时为 `null`。工具调用、耗时和补读次数也有对应的 `common_success_*` 字段。这样不会把某组刚启动就失败、花费很少的尝试误当作省成本。共同成功样本仍有选择范围，必须同时报告它包含多少配对，以及全体尝试各自完成多少，不能只展示这一列好看的成本。

可以用下面的命令打印刚才上下文小实验的配对结果，无需再次请求模型：

```bash
python - <<'PY'
import json
from pathlib import Path

report = json.loads(Path("artifacts/learning-context/report.json").read_text())
print("计划尝试：", len(report["manifest"]), "已记录：", len(report["rows"]))
for name, group in report["pairs"].items():
    print(name, group)
PY
```

完整正式批次还要核对 `manifest` 与 `rows` 数量是否一致，连接错误也留在失败分母中。`stop` 按定义未完成，但属于停止控制，不能当成生产恢复机制失效。首请求的本地 token 估算与供应商返回的全任务 token 用量分开报告；共同历史是预置的，后者不包含它最初生成时的成本。失败若没有返回用量，报表中的零也不能证明网关实际没有消耗。

还要检查评分器有没有遗漏任务要求。本次十二道上下文题要求补充测试，原始分数只检查运行终态与隐藏功能测试，因此另做[测试结构审计](../../artifacts/benchmark-v5-robustness/test_addition_audit.py)：比较初始与最终测试的语法树，检查是否新增或实质修改测试，排除只改注释、删除或原样改名。`completed_with_test_structure` 在原始通过条件上增加这项检查，原始成绩仍保留。语法树变化不能证明断言有用、测试实际运行或完全遵循指令；例如写一个永远成立的断言，仍可能有结构变化。

最终候选的 `verifier.passed` 也要与 `passed` 分开读：隐藏功能测试通过，但模型没有正常结束，仍不算完成任务。遇到这种情况，查看真实请求中是否仍有成功测试结果；不能只凭重复读取就断言上下文把结果裁丢了。

阅读真实成绩时继续回到 [评测说明](../benchmark.md)，不根据这章的小实验推断所有仓库表现。随后读 [运行可靠性](13-runtime-reliability.md) 与 [记忆和 rollout](14-memory-and-rollouts.md)，逐个运行配套回归。后训练会复用环境接口，但需要另行设计训练任务、奖励与留出评测，不能直接拿这里的答案训练再报同一份成绩。
