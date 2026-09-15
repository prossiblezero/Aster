# 上下文与恢复专项任务手册

本专项补上“上下文装得下，但任务是否还能做对”和“运行时提示能否帮助模型脱困”两段证据。代码题均使用独立临时仓库，模型结束后才注入隐藏测试；参考修复不进入模型请求。数据属于 evaluation，不用于后续训练。

## 上下文：相同历史，三档预算

[context_task_cases.json](context_task_cases.json) 有 12 道不同的代码题，每类 3 道；另有 1 个不可恢复信息丢失控制，单独统计。

| 类别 | 题目 | 关键证据在哪里 |
|---|---|---|
| `file_evidence` | 重试延迟、嵌套脱敏、配置合并 | 早期工具观察与规范文件，模型可以补读文件 |
| `history_only` | 名字去重、金额舍入、游标分页 | 用户历史中的要求，当前请求和初始文件不重复这些约束 |
| `multi_file` | 超时单位迁移、禁用任务筛选、统一数据编码 | 历史观察和多份源文件，需保持调用方一致 |
| `corrected_history` | 重试规则、Unicode 标识、队列排序 | 用户后续纠正了旧决定，应使用最新要求 |
| `loss_control` | 一次性协商的路由映射 | 只有旧工具观察；没有文件、工作记忆或长期记忆副本 |

`history` 是初始历史；`history_evidence` 是用于检查实际发送请求的原文片段，不会被执行器额外注入或标记保留；`evidence_paths` 是可重读来源，用于统计成功的 `read_file` 补读，不能据此声称没有通过 search 或 shell 获取证据。用户历史按生产策略保留；工具观察会被裁剪。

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
