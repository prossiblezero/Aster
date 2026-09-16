# 固定预算选择与确认

原题已经用于开发；新批次是同题重复确认，不是未见测试集。全部失败保留，不挑成功样本。
选定输入预算：8192；选择规则与数据哈希见 JSON。

| 阶段 / 输入预算 | 完成 | Pass@1 | Pass@3 | 隐藏功能通过 | 未压缩输入均值 | 实际首请求均值 | 平均首请求压缩 | 供应商总 token |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| artifacts/benchmark-v8-balanced/report.json / 6144 | 22/36 | 61.11% | 83.33% | 36 | 18,434.83 | 5,995.75 | 67.48% | 2,078,361 |
| artifacts/benchmark-v8-budget-8192/report.json / 8192 | 12/12 | 100.00% | — | 12 | 18,434.83 | 7,779.25 | 57.80% | 671,597 |
| artifacts/benchmark-v8-budget-10240/report.json / 10240 | 12/12 | 100.00% | — | 12 | 18,434.83 | 9,879.25 | 46.41% | 758,114 |
| artifacts/benchmark-v8-budget-12288/report.json / 12288 | 11/12 | 91.67% | — | 12 | 18,434.83 | 12,018.25 | 34.81% | 882,107 |
| artifacts/benchmark-v8-confirmation/report.json / 8192 | 36/36 | 100.00% | 100.00% | 36 | 18,434.83 | 7,779.25 | 57.80% | 1,899,376 |

压缩率是逐次本地首请求比例的平均，不是供应商总成本节省。停止原因与控制题单列：

- artifacts/benchmark-v8-balanced/report.json：主任务停止原因 {"verified": 22, "step_limit": 14}；控制题 1/3，停止原因 {"verified": 1, "context_budget_exceeded": 2}。
- artifacts/benchmark-v8-budget-8192/report.json：主任务停止原因 {"verified": 12}；控制题 0/0，停止原因 {}。
- artifacts/benchmark-v8-budget-10240/report.json：主任务停止原因 {"verified": 12}；控制题 0/0，停止原因 {}。
- artifacts/benchmark-v8-budget-12288/report.json：主任务停止原因 {"verified": 11, "step_limit": 1}；控制题 0/0，停止原因 {}。
- artifacts/benchmark-v8-confirmation/report.json：主任务停止原因 {"verified": 36}；控制题 0/3，停止原因 {"step_limit": 3}。

确认是否达到预设门槛：True。未达标时不重新选择预算或重跑失败样本。
