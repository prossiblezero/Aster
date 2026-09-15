# Aster 上下文与恢复专项评测

预置历史/故障前缀，后续由真实模型独立执行；隐藏测试在结束后注入。
恢复的主要对照是 silent 与 feedback：同样允许继续，只改变纠偏提示。stop 不计作提示收益。
所有计划尝试（含网关失败）保留；三次重复不是三道新题。

| 专项 / 类别 / 条件 | 场景 | 尝试 | 完成 | 平均后续工具调用 | 平均总 token | 平均秒 |
|---|---:|---:|---:|---:|---:|---:|
| context/corrected_history/balanced | 3 | 9 | 8 | 12.89 | 57917 | 187.87 |
| context/corrected_history/full | 3 | 9 | 9 | 10.33 | 148990 | 140.87 |
| context/corrected_history/tight | 3 | 9 | 0 | 14.11 | 45506 | 103.37 |
| context/file_evidence/balanced | 3 | 9 | 6 | 17.67 | 60093 | 171.74 |
| context/file_evidence/full | 3 | 9 | 9 | 11.22 | 131791 | 177.82 |
| context/file_evidence/tight | 3 | 9 | 0 | 9.33 | 21872 | 59.21 |
| context/history_only/balanced | 3 | 9 | 9 | 10.00 | 48833 | 99.74 |
| context/history_only/full | 3 | 9 | 9 | 8.89 | 130718 | 126.41 |
| context/history_only/tight | 3 | 9 | 1 | 13.11 | 44900 | 97.18 |
| context/loss_control/balanced | 1 | 3 | 0 | 16.00 | 52327 | 95.91 |
| context/loss_control/full | 1 | 3 | 3 | 5.33 | 81594 | 44.86 |
| context/loss_control/tight | 1 | 3 | 0 | 4.67 | 10879 | 38.25 |
| context/multi_file/balanced | 3 | 9 | 4 | 21.11 | 53906 | 242.54 |
| context/multi_file/full | 3 | 9 | 8 | 15.56 | 139407 | 198.60 |
| context/multi_file/tight | 3 | 9 | 0 | 4.78 | 8971 | 17.55 |
| recovery/oscillation/feedback | 3 | 9 | 9 | 13.22 | 36285 | 112.92 |
| recovery/oscillation/silent | 3 | 9 | 9 | 11.56 | 34815 | 114.33 |
| recovery/oscillation/stop | 3 | 3 | 0 | 0.00 | 0 | 0.00 |
| recovery/output_loop/feedback | 3 | 9 | 9 | 11.33 | 37150 | 82.70 |
| recovery/output_loop/silent | 3 | 9 | 9 | 11.22 | 36054 | 71.30 |
| recovery/output_loop/stop | 3 | 3 | 0 | 0.00 | 0 | 0.00 |
| recovery/repeated_error/feedback | 3 | 9 | 9 | 16.00 | 48868 | 167.53 |
| recovery/repeated_error/silent | 3 | 9 | 9 | 16.78 | 55368 | 190.49 |
| recovery/repeated_error/stop | 3 | 3 | 0 | 0.00 | 0 | 0.00 |
| recovery/repeated_read/feedback | 3 | 9 | 9 | 13.44 | 47041 | 130.86 |
| recovery/repeated_read/silent | 3 | 9 | 9 | 13.22 | 45514 | 116.35 |
| recovery/repeated_read/stop | 3 | 3 | 0 | 0.00 | 0 | 0.00 |

## 同题配对

左右条件顺序见组名；成本仅在双方均成功的配对中比较，完整成功率见上表。

| 类别 / 左 / 右 | 配对 | 都完成 | 仅左完成 | 仅右完成 | 都失败 | 共同成功平均 token 左→右 |
|---|---:|---:|---:|---:|---:|---|
| context/corrected_history/full/balanced | 9 | 8 | 1 | 0 | 0 | 151934 → 52202 |
| context/corrected_history/full/tight | 9 | 0 | 9 | 0 | 0 | — |
| context/file_evidence/full/balanced | 9 | 6 | 3 | 0 | 0 | 132533 → 54359 |
| context/file_evidence/full/tight | 9 | 0 | 9 | 0 | 0 | — |
| context/history_only/full/balanced | 9 | 9 | 0 | 0 | 0 | 130718 → 48833 |
| context/history_only/full/tight | 9 | 1 | 8 | 0 | 0 | 147106 → 57915 |
| context/loss_control/full/balanced | 3 | 0 | 3 | 0 | 0 | — |
| context/loss_control/full/tight | 3 | 0 | 3 | 0 | 0 | — |
| context/multi_file/full/balanced | 9 | 4 | 4 | 0 | 1 | 129618 → 53950 |
| context/multi_file/full/tight | 9 | 0 | 8 | 0 | 1 | — |
| recovery/oscillation/silent/feedback | 9 | 9 | 0 | 0 | 0 | 34815 → 36285 |
| recovery/output_loop/silent/feedback | 9 | 9 | 0 | 0 | 0 | 36054 → 37150 |
| recovery/repeated_error/silent/feedback | 9 | 9 | 0 | 0 | 0 | 55368 → 48868 |
| recovery/repeated_read/silent/feedback | 9 | 9 | 0 | 0 | 0 | 45514 → 47041 |

## 失败记录

- context_config_merge / balanced / #1: step_limit；phase
- context_config_merge / balanced / #3: step_limit；phase
- context_config_merge / tight / #1: context_budget_exceeded；phase, independent_tests
- context_config_merge / tight / #2: context_budget_exceeded；phase, independent_tests
- context_config_merge / tight / #3: context_budget_exceeded；phase
- context_cursor_pagination / tight / #1: context_budget_exceeded；phase
- context_cursor_pagination / tight / #2: context_budget_exceeded；phase
- context_cursor_pagination / tight / #3: context_budget_exceeded；phase
- context_decimal_rounding / tight / #1: context_budget_exceeded；phase
- context_decimal_rounding / tight / #2: context_budget_exceeded；phase
- context_decimal_rounding / tight / #3: context_budget_exceeded；phase
- context_disabled_jobs / balanced / #1: step_limit；phase
- context_disabled_jobs / tight / #1: context_budget_exceeded；phase, independent_tests
- context_disabled_jobs / tight / #2: context_budget_exceeded；phase, independent_tests
- context_disabled_jobs / tight / #3: context_budget_exceeded；phase, independent_tests
- context_ephemeral_route / balanced / #1: step_limit；phase, independent_tests
- context_ephemeral_route / balanced / #2: step_limit；phase, independent_tests
- context_ephemeral_route / balanced / #3: step_limit；phase, independent_tests
- context_ephemeral_route / tight / #1: context_budget_exceeded；phase, independent_tests
- context_ephemeral_route / tight / #2: context_budget_exceeded；phase, independent_tests
- context_ephemeral_route / tight / #3: context_budget_exceeded；phase, independent_tests
- context_name_deduplication / tight / #1: context_budget_exceeded；phase
- context_name_deduplication / tight / #2: context_budget_exceeded；phase
- context_nested_redaction / balanced / #1: step_limit；phase
- context_nested_redaction / tight / #1: context_budget_exceeded；phase, independent_tests
- context_nested_redaction / tight / #2: context_budget_exceeded；phase, independent_tests
- context_nested_redaction / tight / #3: context_budget_exceeded；phase, independent_tests
- context_ordering_correction / tight / #1: context_budget_exceeded；phase
- context_ordering_correction / tight / #2: step_limit；phase
- context_ordering_correction / tight / #3: context_budget_exceeded；phase
- context_retry_correction / balanced / #2: turn_limit；phase
- context_retry_correction / tight / #1: step_limit；phase
- context_retry_correction / tight / #2: context_budget_exceeded；phase
- context_retry_correction / tight / #3: step_limit；phase, independent_tests
- context_retry_delay / tight / #1: context_budget_exceeded；phase
- context_retry_delay / tight / #2: context_budget_exceeded；phase
- context_retry_delay / tight / #3: context_budget_exceeded；phase
- context_shared_encoding / balanced / #1: step_limit；phase
- context_shared_encoding / balanced / #2: step_limit；phase
- context_shared_encoding / full / #1: step_limit；phase
- context_shared_encoding / tight / #1: context_budget_exceeded；phase, independent_tests
- context_shared_encoding / tight / #2: context_budget_exceeded；phase, independent_tests
- context_shared_encoding / tight / #3: context_budget_exceeded；phase, independent_tests
- context_timeout_migration / balanced / #1: step_limit；phase
- context_timeout_migration / balanced / #2: step_limit；phase
- context_timeout_migration / tight / #1: context_budget_exceeded；phase, independent_tests
- context_timeout_migration / tight / #2: context_budget_exceeded；phase, independent_tests
- context_timeout_migration / tight / #3: context_budget_exceeded；phase, independent_tests
- context_unicode_correction / tight / #1: context_budget_exceeded；phase
- context_unicode_correction / tight / #2: context_budget_exceeded；phase, independent_tests
- context_unicode_correction / tight / #3: context_budget_exceeded；phase, independent_tests
- missing_add-endpoint-with-test / stop / #1: stall_recovery_exhausted；phase, independent_tests
- missing_dependency-upgrade / stop / #1: stall_recovery_exhausted；phase, independent_tests
- missing_multi-file-refactor / stop / #1: stall_recovery_exhausted；phase, independent_tests
- oscillate_config-migration / stop / #1: stall_recovery_exhausted；phase, independent_tests
- oscillate_debug-and-fix / stop / #1: stall_recovery_exhausted；phase, independent_tests
- oscillate_dependency-upgrade / stop / #1: stall_recovery_exhausted；phase, independent_tests
- output_add-endpoint-with-test / stop / #1: stall_recovery_exhausted；phase, independent_tests
- output_config-migration / stop / #1: stall_recovery_exhausted；phase, independent_tests
- output_debug-and-fix / stop / #1: stall_recovery_exhausted；phase, independent_tests
- repeat_add-endpoint-with-test / stop / #1: stall_recovery_exhausted；phase, independent_tests
- repeat_debug-and-fix / stop / #1: stall_recovery_exhausted；phase, independent_tests
- repeat_multi-file-refactor / stop / #1: stall_recovery_exhausted；phase, independent_tests

stop 的预期停止保留在失败列表中；它是控制组，不是运行时故障。
输入缩减是本地估算；总 token 来自供应商 usage，不包含预置历史原本的生成成本。
故障由实验注入，不据此推断自然运行中的停滞发生率。每次实际请求、执行记录、候选代码和隐藏评分均归档。
