# Aster 上下文与恢复专项评测

预置历史/故障前缀，后续由真实模型独立执行；隐藏测试在结束后注入。
恢复的主要对照是 silent 与 feedback：同样允许继续，只改变纠偏提示。stop 不计作提示收益。
所有计划尝试（含网关失败）保留；三次重复不是三道新题。

| 专项 / 类别 / 条件 | 场景 | 尝试 | 完成 | 平均后续工具调用 | 平均总 token | 平均秒 |
|---|---:|---:|---:|---:|---:|---:|
| context/file_evidence/full | 1 | 1 | 1 | 12.00 | 149505 | 420.23 |
| context/file_evidence/tight | 1 | 1 | 0 | 4.00 | 7019 | 52.42 |
| context/loss_control/full | 1 | 1 | 1 | 6.00 | 81730 | 231.18 |
| context/loss_control/tight | 1 | 1 | 0 | 4.00 | 7015 | 51.15 |
| recovery/output_loop/feedback | 1 | 1 | 1 | 9.00 | 25527 | 192.29 |
| recovery/output_loop/silent | 1 | 1 | 1 | 8.00 | 25183 | 215.35 |
| recovery/output_loop/stop | 1 | 1 | 0 | 0.00 | 0 | 0.00 |

## 同题配对

左右条件顺序见组名；成本仅在双方均成功的配对中比较，完整成功率见上表。

| 类别 / 左 / 右 | 配对 | 都完成 | 仅左完成 | 仅右完成 | 都失败 | 共同成功平均 token 左→右 |
|---|---:|---:|---:|---:|---:|---|
| context/file_evidence/full/tight | 1 | 0 | 1 | 0 | 0 | — |
| context/loss_control/full/tight | 1 | 0 | 1 | 0 | 0 | — |
| recovery/output_loop/silent/feedback | 1 | 1 | 0 | 0 | 0 | 25183 → 25527 |

## 失败记录

- context_ephemeral_route / tight / #1: context_budget_exceeded；phase, independent_tests
- context_retry_delay / tight / #1: context_budget_exceeded；phase, independent_tests
- output_debug-and-fix / stop / #1: stall_recovery_exhausted；phase, independent_tests

stop 的预期停止保留在失败列表中；它是控制组，不是运行时故障。
输入缩减是本地估算；总 token 来自供应商 usage，不包含预置历史原本的生成成本。
故障由实验注入，不据此推断自然运行中的停滞发生率。每次实际请求、执行记录、候选代码和隐藏评分均归档。
