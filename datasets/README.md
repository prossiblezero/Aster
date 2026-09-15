# Aster 评测数据集

当前数据集有 **262 个不同编号的任务或机制场景**：193 条主任务，加 69 个机制场景。另有 **12 组上下文压力配置、16 组工具可见性配置**。配置数、重复运行数、同题不同开关的尝试数，以及 `tests/` 中的单元测试，都不算新增任务。

| 数据范围 | 数量 | 定义位置 |
|---|---:|---|
| Harness 行为 | 12 条任务 | `benchmark_tasks.json` |
| 仓库修改 | 5 条任务 | `benchmark_tasks.json` |
| 跨运行记忆问答 | 12 条任务 | `benchmark_tasks.json` |
| HumanEval 函数实现 | 164 条任务 | `benchmark_tasks.json` |
| 记忆准入、来源新鲜度、反馈与回滚 | 24 个场景 | `memory_evolution_cases.json` |
| 恢复、Hooks、终止与续跑 | 14 个场景 | `runtime_cases.json` |
| RL 环境奖励与重置边界 | 4 个场景 | `runtime_cases.json` |
| 测试工具真实执行与失败分类 | 27 个场景 | `tool_contract_cases.json` |
| 上下文压力 | 12 组配置，单列 | `context_cases.json` |
| 工具可见性 | 16 组配置，分别检查 core/all | `tool_visibility_cases.json` |

先看 [任务手册](benchmark.md)，再看 [评测方法与结果](../docs/benchmark.md)。任务总数多不等于长任务能力强：164 条函数题用于代码正确性覆盖，5 条仓库题用于多文件工具执行；两类分别统计。

| 文件或目录 | 用途 |
|---|---|
| `benchmark_tasks.json` | 全部任务的 prompt、工作区模板、执行上限、评分规则及参考动作 |
| `fixtures/` | 每条任务复制到临时工作区的文件；模型只能看到这部分 |
| `checks/` | 模型运行结束后才加入隔离副本的独立验证器 |
| `context_cases.json` | 代码、日志、中文三类内容 × 四种历史长度，以及预算和保留证据 |
| `memory_evolution_cases.json` | 12 个准入、4 个来源新鲜度、6 个反馈调整、2 个快照回滚场景 |
| `runtime_cases.json` | 14 个 Runtime 场景与 4 个 RL 接口场景；操作、条件开关、期望结果都在 JSON 中 |
| `tool_contract_cases.json` | pytest/unittest 的通过、跳过、伪输出、缺报告、子测试与环境错误；包含真实 fixture 与期望状态 |
| `tool_visibility_cases.json` | 功能开关、审批、计划与隔离组合下的工具目录和执行校验；可见性矩阵不计入自主任务数量 |
| `real_subset.json` | 在调用真实模型前固定的 29 条任务名单：5 条仓库、12 条记忆、HumanEval/0–11 |
| `SOURCES.md` | 来源、版本、许可证和对原任务的调整 |
| `public_benchmarks.md` | HumanEval / SWE-bench 的用法，以及后续训练数据的隔离原则 |
| `legacy/` | v1 的小样例和旧参数矩阵，当前执行器不加载 |

## 如何读一条任务

`id` 是稳定编号；`suite` 是任务组；`split=evaluation` 表示只用于评测；`prompt` 是模型接到的要求；`fixture` 指向初始仓库。`max_steps` 限制工具调用次数，`max_turns` 限制模型请求轮数，一轮可能产生多个调用。

`require_edit` 要求实际修改，`require_verification` 要求修改后运行验证。`checks` 是评分标准：`verifier` 指向独立 pytest 测试，`contains/absent` 检查文件文本，`answer_value` 检查问答 JSON 的值，`unchanged` 检查工作区没有被修改，`events/outcome_reasons` 检查真实运行记录。恢复任务的 `setup` 指定中断后的漂移或配置变化；`settings` 是这个测试预先固定的运行配置。

`actions` 是确定性检查用的参考动作，**不交给真实模型**。`$last_evidence` 由执行器换成前一个实际工具事件的证据编号；不允许凭空伪造一个编号充当证据。记忆任务的 `source_path` 指出学习阶段要读取的文件，`fact` 供人检查题目，`query` 用于受控的记忆标记；回答策略只从实际观察中提取信息。

机制场景的 `kind` 决定运行哪一种检查；`operations` 是工具调用及结束、恢复、重置操作；`variants` 指定开启/关闭条件。`expected` 检查该条件下声明的行为，`goal` 则让不同条件面对同一目标。例如，关闭重复调用 Hooks 时，脚本预期副作用执行两次，所以“符合关闭组预期”不代表“实现了只执行一次的目标”。报告分开显示这两个数字。

记忆场景中的 `observation` 是证据摘录，`source` 是来源文件的初始内容，执行器写入后计算哈希；`uses` 是预先标注的成功/失败反馈，`consolidate` 表示执行 Dream 整理。`expected_on/off` 是各条件下预期召回的内容。6 个反馈场景验证排序与淘汰策略，不能充当真实模型的自进化效果。两个开关条件都保留来源检查和敏感内容拒绝，也都保留会话工作记忆；关闭的是长期记忆的反馈演化。

运行主任务使用 `python scripts/benchmark.py`，运行机制场景使用 `python scripts/benchmark_mechanisms.py`。后者共执行 97 次：24 个记忆场景各两个条件，加 18 个 Runtime/RL 场景及其中 4 个场景的关闭条件，再加 27 个测试工具场景。69 个场景不会因为执行 97 次而变成 97 道题。

测试工具场景中，`command` 是测试命令，`files` 是临时仓库的文件内容，`status/reason` 是预期结果。执行器读取这个 JSON，与单元测试共用同一题目定义；真实运行日志归档到机制报告的 `runs/` 中。

`memory_feedback: neutral` 表示该案例还要验证长期记忆的成功、失败使用次数都不增加。分类保持保守：加载、准备、清理、执行异常和缺报告没有证明记忆有害；任务仍不能通过验证。只有明确的测试失败结果才用于失败反馈。

## 工具可见性配置矩阵

`tool_visibility_cases.json` 的 16 组配置分别在 `core` 和 `all` 下执行，共 32 次配置检查。覆盖关闭 Worker、长期记忆、问答回调、写入范围、计划模式、关闭隔离与缺少审批入口，以及功能同时关闭的情况；另检查仅关闭记忆演化或自动整理时，不应误隐藏正常记忆工具。

`settings` 是在基础配置上覆盖的字段；`mode` 是运行模式；`question_callback=false` 模拟没有人工问答入口；`unavailable` 把预期拒绝原因映射到工具名称；`probes` 给出直接调用的合法参数。每组同时核对模型定义、加载目录、已经加载过的工具及直接调用，并检查禁用工具不能被部分加载。

```bash
conda activate aster
python -m pytest -q tests/test_tool_contracts.py -k disabled_capabilities
python -m pytest -q tests/test_review_regressions.py -k core_catalog_loads
```

第二条通过真实 Runtime 的连续模型轮次，验证首轮只有核心定义，加载一个可选工具后下一轮才能看到其参数并执行。模型使用固定策略，因此这是工具发现与执行的集成验证，不是模型自主选择工具的能力分数。

## 独立验证器的边界

代码任务结束后，执行器把候选代码复制到评分工作区，再放入模型没有见过的测试。评分固定 pytest 配置、禁用工作区 `conftest.py` 和插件自动加载，以隔离模式启动 Python。父进程读取结构化 JUnit 报告，核对全部预期隐藏测试名称、数量和结果；假输出、跳过关键检查、缺报告、空测试、采集错误、超时与非零退出都不算通过。初始版本和参考解分别检查目标失败、原有行为和修复结果，详细约束见 [评分边界](../docs/grader-boundary.md)。

这些措施覆盖了改公开测试、改配置、替换评分入口等具体问题，**不能保证任意恶意 Python 代码都无法干扰评分进程**。候选代码和 pytest 仍在同一个受沙箱限制的 Python 进程内执行；沙箱限制文件与网络权限，不等于为测试解释器提供完全防作弊的隔离。RL 接口只在结束时调用独立评分回调，仓库自报测试成功不能直接获得成功奖励；接入后训练时，还需要为实际训练任务配置隔离的评分器并补充攻击测试。

## 评测数据不进入训练

以上主任务、机制场景及其参考动作、模型轨迹全部属于 `evaluation`；上下文配置也是评测用途。它们不能直接用于后续 SFT、GRPO，也不能简单换个变量名后当作未见测试。后训练另建按仓库和问题家族隔离的数据清单。公开 HumanEval 可能已被基础模型见过，当前结果不用于证明模型对未见题目的泛化；RL 接口检查也不等于完成了后训练。

## 新增任务的最小步骤

1. 在 `fixtures/` 放一个足够小、可独立复制的工作区。
2. 在任务 JSON 中写清要求、步数上限和评分规则。
3. 代码题在 `checks/` 写 `test_fix`（目标行为）和 `test_regression`（须保留的行为），并提供能通过的参考动作。函数题的 regression 只保护函数可调用，不代表完整回归覆盖。
4. 运行 `python scripts/benchmark.py --task 任务编号`：初始版本必须在目标测试上失败，参考动作后必须通过；收集错误、没有测试、超时都不能算通过。
5. 真实模型实验前固定选择名单。每题每个条件都从同一个干净模板出发，保留失败尝试。

测试断言使用 Python 是为了自动执行；题目、参数、参考动作都在数据目录中，执行脚本不内嵌题目。
