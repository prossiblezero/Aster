# 上下文与恢复专项任务手册

本专项补上“上下文装得下，但任务是否还能做对”和“运行时提示能否帮助模型脱困”两段证据。代码题均使用独立临时仓库，模型结束后才注入隐藏测试；参考修复不进入模型请求。数据属于 evaluation，不用于后续训练。

## 上下文：相同历史，三档预算

[context_task_cases.json](context_task_cases.json) 有 12 道不同的代码题，每类 3 道；另有 1 个一次性信息控制，单独统计。

| 类别 | 题目 | 关键证据在哪里 |
|---|---|---|
| `file_evidence` | 重试延迟、嵌套脱敏、配置合并 | 早期工具观察与规范文件，模型可以补读文件 |
| `history_only` | 名字去重、金额舍入、游标分页 | 用户历史中的要求，当前请求和初始文件不重复这些约束 |
| `multi_file` | 超时单位迁移、禁用任务筛选、统一数据编码 | 历史观察和多份源文件，需保持调用方一致 |
| `corrected_history` | 重试规则、Unicode 标识、队列排序 | 用户后续纠正了旧决定，应使用最新要求 |
| `loss_control` | 一次性协商的路由映射 | 只有旧工具观察；没有文件、工作记忆或长期记忆副本 |

`history` 是初始历史；`history_evidence` 是用于检查实际发送请求的原文片段，不会被执行器额外注入或标记保留；`evidence_paths` 是可重读来源，用于统计成功的 `read_file` 补读，不能据此声称没有通过 search 或 shell 获取证据。用户历史按生产策略保留；工具观察会被裁剪。

`loss_control` 的题目、历史与隐藏测试固定：在 v5 中，旧工具观察被删除后没有回读入口，因此作为信息丢失控制。重构增加观察归档后，这道原题也用于检查模型能否自主找回一次性证据；它不是新增题，不改评分，不并入 12 道主任务的完成率。

[context_history.json](context_history.json) 提供共同的长历史：36 条固定代码、日志、中文观察，模拟之前完成的无关排查。所有条件使用相同内容和顺序，不是让模型现场生成一段长任务，也不把历史生成成本计入后续任务费用。

| 条件 | 总窗口配置 | 输出预留 | 实际输入上限 |
|---|---:|---:|---:|
| `full` | 32,768 | 2,048 | 30,720 |
| `balanced` | 8,192 | 2,048 | 6,144 |
| `tight` | 5,632 | 2,048 | 3,584 |

三个条件仅改变 `context_tokens`。模型、输出预留、单工具正文上限、记忆配置、工具定义和动作预算相同。逐次检查真实请求是否删除历史；全量组也检查有没有发生裁剪。首请求的本地 token 缩减与全任务供应商 token 分别统计；某组失败提前结束，不能把少花的 token 宣称为效率收益。

6,144 这一中间档来自预实验：3,584 能容纳初始输入，但模型的最近一组完整工具调用与结果有时已超出预算。正式运行前固定三档，不称中间档为最优预算。预实验与正式报告分开保留。

## 恢复：同样允许继续，仅改变提示

[recovery_task_cases.json](recovery_task_cases.json) 定义 12 个故障场景，复用主任务库中的 5 道仓库修改题。重复读取、交替振荡、重复错误、不同命令产生相同失败输出各 3 个场景。

`base_task` 引用原题；`seed_actions` 是用真实工具制造停滞的前缀；`expected_trigger` 要求前缀确实触发指定检测原因。前缀通过 Runtime 的模型轮次执行，保留完整调用/结果配对。随后整体替换为真实模型，模型自己决定所有后续动作。

| 条件 | 恢复额度 | 注入纠偏提示 | 用途 |
|---|---:|---|---|
| `feedback` | 2 | 是 | 生产机制 |
| `silent` | 2 | 否 | 主要对照：同样允许继续，只消除恢复消息 |
| `stop` | 0 | 否 | 停止边界参照；不将机械停机差异当作提示收益 |

前缀会使用一次恢复额度，两种继续组均剩一次。静默组只在实验脚本的 Runtime 子类中拦截恢复消息，保留检测器、计数、清窗、安全、检查点和其他反馈。未增加生产配置字段。

每个继续条件运行 3 次，停止条件运行 1 次，因此恢复部分共 84 次尝试；上下文 13 个场景 × 3 档 × 3 次，共 117 次。总计 201 次计划尝试，其中 12 次为不请求模型的停止参照。12 个故障场景并不等于 12 道新增独立代码题。

`first_call_changed` 使用规范化参数判断首次自主调用是否不同，只是行为观察；改用另一工具仍可能失败。`renewed_stalls` 记录接手后再次检测到停滞的次数。是否完成任务由终态与隐藏测试共同决定。两组提示效果没有差异也是有效结果；故障由实验注入，不据此推断自然任务的停滞发生率。

## 运行与核对

```bash
conda activate aster
python scripts/benchmark_robustness.py --output artifacts/benchmark-v5-preflight
python scripts/benchmark_robustness.py --real --concurrency 8 --output artifacts/benchmark-v5-robustness
python scripts/benchmark_mechanisms.py --output artifacts/benchmark-v5-mechanisms
python -m pytest -q tests/test_robustness_benchmark.py tests/test_recovery_contracts.py
```

`--real` 也会先检查原题失败、已有行为通过、参考修复通过以及前缀/预算条件；预检失败时不调用模型。`--suite context|recovery` 选择专项，`--task` 选择具体场景，`--repeats` 可覆盖协议默认重复数。正式运行会先保存全部计划尝试的 manifest；保留每次 request、trace、checkpoint、候选代码、diff 和隐藏测试结果，连接错误也计入报告。

题目使用 Python 测试作为可执行判分标准，判断行为而非候选代码必须包含参考解字串。所有源任务、试跑和报告版本保留，不用后续好看的批次覆盖失败。

## 上下文重构后的原题回归

仅重跑上下文专项，仍用原来的 13 个场景、三档预算和每档三次重复，共 117 次。12 道主任务共 108 次，信息控制另计 9 次。预检验证超预算输入实际发生压缩并满足请求预算；不要求一定删掉整条消息，因为缩短正文也属于有效压缩。

```bash
conda run --no-capture-output -n aster python scripts/benchmark_robustness.py --real --suite context --repeats 3 --concurrency 8 --output artifacts/context-rerun
conda run --no-capture-output -n aster python scripts/compare_context_runs.py --before artifacts/benchmark-v5-robustness/report.json --after artifacts/context-rerun/report.json --output artifacts/context-rerun
```

这里使用新输出目录，避免覆盖已保存的正式批次。v6 对应 `54e1900`，v7 对应 `67e4415`；各自的 `launch.json` 记录完整运行命令及隔离工作目录。正式运行通过 `PYTHONPATH` 指向冻结工作目录的 `src`，避免 Conda 环境中的 editable install 导入正在修改的主目录。结果与版本取舍见[上下文重构实测](../docs/context-results.md)。

新报告记录题目、长历史、初始仓库和隐藏判分器的独立 SHA-256。比较脚本从 v5 冻结的 Git 提交恢复原文件哈希，并核对相同的模型、设置和实验协议；丢失任务、缺少尝试、重复记录或输入变更会拒绝比较。它分别报告主任务、各题型和信息控制的 Pass@1／Pass@3、隐藏功能通过数、预算停止数，以及双方都成功时的工具／token／耗时成本。`observation_read_calls` 统计归档工具查询或读取的调用次数，`observation_read_successes` 统计成功返回次数，不直接证明读到的内容帮助完成任务。

这是历史版本与重构版本的回归比较，不能消除跨时间的模型或网关波动。v5 的 117 次上下文尝试共记录 8,470,543 tokens，累计任务耗时约 4.21 小时；并发 8 时理想运行约 32 分钟，实际时间和新版本成本以本次记录为准。未记录网关单价，不据此推算美元费用。原题已用于定位问题，属于开发回归证据，不能代替未参与调参的泛化评测。
