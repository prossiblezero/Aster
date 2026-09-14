# Aster 评测数据集

当前题库有 **193 条独立任务**：12 条 Harness 行为、5 条仓库修改、12 条跨运行记忆、164 条 HumanEval 函数实现。另有 **12 组上下文压力配置**。配置数、重复运行数、同题不同开关的尝试数，都不算新任务。

先看 [任务手册](benchmark.md)，再看 [评测方法与结果](../docs/benchmark.md)。任务总数多不等于长任务能力强：164 条函数题用于代码正确性覆盖，5 条仓库题用于多文件工具执行；两类分别统计。

| 文件或目录 | 用途 |
|---|---|
| `benchmark_tasks.json` | 全部任务的 prompt、工作区模板、执行上限、评分规则及参考动作 |
| `fixtures/` | 每条任务复制到临时工作区的文件；模型只能看到这部分 |
| `checks/` | 模型运行结束后才加入隔离副本的独立验证器 |
| `context_cases.json` | 代码、日志、中文三类内容 × 四种历史长度，以及预算和保留证据 |
| `real_subset.json` | 在调用真实模型前固定的 29 条任务名单：5 条仓库、12 条记忆、HumanEval/0–11 |
| `SOURCES.md` | 来源、版本、许可证和对原任务的调整 |
| `public_benchmarks.md` | HumanEval / SWE-bench 的用法，以及后续训练数据的隔离原则 |
| `legacy/` | v1 的小样例和旧参数矩阵，当前执行器不加载 |

## 如何读一条任务

`id` 是稳定编号；`suite` 是任务组；`split=evaluation` 表示只用于评测；`prompt` 是模型接到的要求；`fixture` 指向初始仓库。`max_steps` 限制工具调用次数，`max_turns` 限制模型请求轮数，一轮可能产生多个调用。

`require_edit` 要求实际修改，`require_verification` 要求修改后运行验证。`checks` 是评分标准：`verifier` 指向独立 pytest 测试，`contains/absent` 检查文件文本，`answer_value` 检查问答 JSON 的值，`unchanged` 检查工作区没有被修改，`events/outcome_reasons` 检查真实运行记录。恢复任务的 `setup` 指定中断后的漂移或配置变化；`settings` 是这个测试预先固定的运行配置。

`actions` 是确定性检查用的参考动作，**不交给真实模型**。`$last_evidence` 由执行器换成前一个实际工具事件的证据编号；不允许凭空伪造一个编号充当证据。记忆任务的 `source_path` 指出学习阶段要读取的文件，`fact` 供人检查题目，`query` 用于受控的记忆标记；回答策略只从实际观察中提取信息。

## 新增任务的最小步骤

1. 在 `fixtures/` 放一个足够小、可独立复制的工作区。
2. 在任务 JSON 中写清要求、步数上限和评分规则。
3. 代码题在 `checks/` 写 `test_fix`（目标行为）和 `test_regression`（须保留的行为），并提供能通过的参考动作。函数题的 regression 只保护函数可调用，不代表完整回归覆盖。
4. 运行 `python scripts/benchmark.py --task 任务编号`：初始版本必须在目标测试上失败，参考动作后必须通过；收集错误、没有测试、超时都不能算通过。
5. 真实模型实验前固定选择名单。每题每个条件都从同一个干净模板出发，保留失败尝试。

测试断言使用 Python 是为了自动执行；题目、参数、参考动作都在数据目录中，执行脚本不内嵌题目。
