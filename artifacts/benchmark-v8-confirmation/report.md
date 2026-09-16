# Aster 上下文与恢复专项评测

预置历史/故障前缀，后续由真实模型独立执行；隐藏测试在结束后注入。
恢复的主要对照是 silent 与 feedback：同样允许继续，只改变纠偏提示。stop 不计作提示收益。
所有计划尝试（含网关失败）保留；每题每条件重复 3 次，不视为新增题目。

| 专项 / 类别 / 条件 | 场景 | 尝试 | 完成 | 平均后续工具调用 | 平均总 token | 平均秒 |
|---|---:|---:|---:|---:|---:|---:|
| context/corrected_history/input_8192 | 3 | 9 | 9 | 9.67 | 51438 | 55.66 |
| context/file_evidence/input_8192 | 3 | 9 | 9 | 11.67 | 52874 | 72.01 |
| context/history_only/input_8192 | 3 | 9 | 9 | 9.22 | 49046 | 50.10 |
| context/loss_control/input_8192 | 1 | 3 | 0 | 16.00 | 61011 | 87.87 |
| context/multi_file/input_8192 | 3 | 9 | 9 | 17.00 | 57685 | 63.93 |

## 同题配对

左右条件顺序见组名；成本仅在双方均成功的配对中比较，完整成功率见上表。

| 类别 / 左 / 右 | 配对 | 都完成 | 仅左完成 | 仅右完成 | 都失败 | 共同成功平均 token 左→右 |
|---|---:|---:|---:|---:|---:|---|

## 失败记录

- context_ephemeral_route / input_8192 / #1: step_limit；phase, independent_tests
- context_ephemeral_route / input_8192 / #2: step_limit；phase, independent_tests
- context_ephemeral_route / input_8192 / #3: step_limit；phase, independent_tests

stop 的预期停止保留在失败列表中；它是控制组，不是运行时故障。
输入缩减是本地估算；总 token 来自供应商 usage，不包含预置历史原本的生成成本。
故障由实验注入，不据此推断自然运行中的停滞发生率。每次实际请求、执行记录、候选代码和隐藏评分均归档。
