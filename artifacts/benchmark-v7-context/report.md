# Aster 上下文与恢复专项评测

预置历史/故障前缀，后续由真实模型独立执行；隐藏测试在结束后注入。
恢复的主要对照是 silent 与 feedback：同样允许继续，只改变纠偏提示。stop 不计作提示收益。
所有计划尝试（含网关失败）保留；三次重复不是三道新题。

| 专项 / 类别 / 条件 | 场景 | 尝试 | 完成 | 平均后续工具调用 | 平均总 token | 平均秒 |
|---|---:|---:|---:|---:|---:|---:|
| context/corrected_history/balanced | 3 | 9 | 9 | 11.89 | 48699 | 104.28 |
| context/corrected_history/full | 3 | 9 | 9 | 9.67 | 140854 | 108.80 |
| context/corrected_history/tight | 3 | 9 | 0 | 3.33 | 8315 | 9.82 |
| context/file_evidence/balanced | 3 | 9 | 6 | 17.44 | 59105 | 161.17 |
| context/file_evidence/full | 3 | 9 | 9 | 11.33 | 166293 | 169.73 |
| context/file_evidence/tight | 3 | 9 | 0 | 3.78 | 7149 | 4.96 |
| context/history_only/balanced | 3 | 9 | 8 | 12.22 | 49950 | 105.77 |
| context/history_only/full | 3 | 9 | 9 | 9.89 | 133004 | 107.59 |
| context/history_only/tight | 3 | 9 | 0 | 3.11 | 7116 | 12.66 |
| context/loss_control/balanced | 1 | 3 | 0 | 16.00 | 49011 | 123.70 |
| context/loss_control/full | 1 | 3 | 3 | 5.33 | 95737 | 73.50 |
| context/loss_control/tight | 1 | 3 | 0 | 5.00 | 10942 | 14.41 |
| context/multi_file/balanced | 3 | 9 | 5 | 21.44 | 53641 | 132.58 |
| context/multi_file/full | 3 | 9 | 9 | 15.33 | 147563 | 120.93 |
| context/multi_file/tight | 3 | 9 | 0 | 5.22 | 10552 | 17.43 |

## 同题配对

左右条件顺序见组名；成本仅在双方均成功的配对中比较，完整成功率见上表。

| 类别 / 左 / 右 | 配对 | 都完成 | 仅左完成 | 仅右完成 | 都失败 | 共同成功平均 token 左→右 |
|---|---:|---:|---:|---:|---:|---|
| context/corrected_history/full/balanced | 9 | 9 | 0 | 0 | 0 | 140854 → 48699 |
| context/corrected_history/full/tight | 9 | 0 | 9 | 0 | 0 | — |
| context/file_evidence/full/balanced | 9 | 6 | 3 | 0 | 0 | 173330 → 52730 |
| context/file_evidence/full/tight | 9 | 0 | 9 | 0 | 0 | — |
| context/history_only/full/balanced | 9 | 8 | 1 | 0 | 0 | 133410 → 45901 |
| context/history_only/full/tight | 9 | 0 | 9 | 0 | 0 | — |
| context/loss_control/full/balanced | 3 | 0 | 3 | 0 | 0 | — |
| context/loss_control/full/tight | 3 | 0 | 3 | 0 | 0 | — |
| context/multi_file/full/balanced | 9 | 5 | 4 | 0 | 0 | 134371 → 49730 |
| context/multi_file/full/tight | 9 | 0 | 9 | 0 | 0 | — |

## 失败记录

- context_config_merge / balanced / #2: step_limit；phase
- context_config_merge / balanced / #3: step_limit；phase
- context_config_merge / tight / #1: context_budget_exceeded；phase, independent_tests
- context_config_merge / tight / #2: context_budget_exceeded；phase, independent_tests
- context_config_merge / tight / #3: context_budget_exceeded；phase, independent_tests
- context_cursor_pagination / balanced / #1: step_limit；phase
- context_cursor_pagination / tight / #1: context_budget_exceeded；phase, independent_tests
- context_cursor_pagination / tight / #2: context_budget_exceeded；phase, independent_tests
- context_cursor_pagination / tight / #3: context_budget_exceeded；phase, independent_tests
- context_decimal_rounding / tight / #1: context_budget_exceeded；phase, independent_tests
- context_decimal_rounding / tight / #2: context_budget_exceeded；phase, independent_tests
- context_decimal_rounding / tight / #3: context_budget_exceeded；phase, independent_tests
- context_disabled_jobs / balanced / #1: step_limit；phase, independent_tests
- context_disabled_jobs / tight / #1: context_budget_exceeded；phase, independent_tests
- context_disabled_jobs / tight / #2: context_budget_exceeded；phase, independent_tests
- context_disabled_jobs / tight / #3: context_budget_exceeded；phase, independent_tests
- context_ephemeral_route / balanced / #1: step_limit；phase, independent_tests
- context_ephemeral_route / balanced / #2: step_limit；phase, independent_tests
- context_ephemeral_route / balanced / #3: step_limit；phase, independent_tests
- context_ephemeral_route / tight / #1: context_budget_exceeded；phase, independent_tests
- context_ephemeral_route / tight / #2: context_budget_exceeded；phase, independent_tests
- context_ephemeral_route / tight / #3: context_budget_exceeded；phase, independent_tests
- context_name_deduplication / tight / #1: context_budget_exceeded；phase, independent_tests
- context_name_deduplication / tight / #2: context_budget_exceeded；phase, independent_tests
- context_name_deduplication / tight / #3: context_budget_exceeded；phase, independent_tests
- context_nested_redaction / balanced / #2: step_limit；phase
- context_nested_redaction / tight / #1: context_budget_exceeded；phase, independent_tests
- context_nested_redaction / tight / #2: context_budget_exceeded；phase, independent_tests
- context_nested_redaction / tight / #3: context_budget_exceeded；phase, independent_tests
- context_ordering_correction / tight / #1: context_budget_exceeded；phase, independent_tests
- context_ordering_correction / tight / #2: context_budget_exceeded；phase, independent_tests
- context_ordering_correction / tight / #3: context_budget_exceeded；phase, independent_tests
- context_retry_correction / tight / #1: context_budget_exceeded；phase, independent_tests
- context_retry_correction / tight / #2: context_budget_exceeded；phase, independent_tests
- context_retry_correction / tight / #3: context_budget_exceeded；phase, independent_tests
- context_retry_delay / tight / #1: context_budget_exceeded；phase, independent_tests
- context_retry_delay / tight / #2: context_budget_exceeded；phase, independent_tests
- context_retry_delay / tight / #3: context_budget_exceeded；phase, independent_tests
- context_shared_encoding / balanced / #3: step_limit；phase
- context_shared_encoding / tight / #1: context_budget_exceeded；phase, independent_tests
- context_shared_encoding / tight / #2: context_budget_exceeded；phase, independent_tests
- context_shared_encoding / tight / #3: context_budget_exceeded；phase, independent_tests
- context_timeout_migration / balanced / #1: step_limit；phase
- context_timeout_migration / balanced / #3: step_limit；phase
- context_timeout_migration / tight / #1: context_budget_exceeded；phase, independent_tests
- context_timeout_migration / tight / #2: context_budget_exceeded；phase
- context_timeout_migration / tight / #3: context_budget_exceeded；phase, independent_tests
- context_unicode_correction / tight / #1: context_budget_exceeded；phase, independent_tests
- context_unicode_correction / tight / #2: context_budget_exceeded；phase
- context_unicode_correction / tight / #3: context_budget_exceeded；phase, independent_tests

stop 的预期停止保留在失败列表中；它是控制组，不是运行时故障。
输入缩减是本地估算；总 token 来自供应商 usage，不包含预置历史原本的生成成本。
故障由实验注入，不据此推断自然运行中的停滞发生率。每次实际请求、执行记录、候选代码和隐藏评分均归档。
