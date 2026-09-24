# Worker 等待修复复测（2026-09-24）

在 `pre-worker-repair-20260924` 标签保存原版本后，提交 `9edb23b` 给 `worker_status` 增加可选 `wait_seconds`（0–60）。实现使用 `asyncio.shield`：等待超时只返回当前状态，不取消子任务。相关 Worker/Runtime/工具契约回归 **117 项通过**，产品功能和训练接口没有其他修改。

复测沿用 2026-09-22 的两道明确委派题和预算，使用同一 GPT Responses 配置。每题重新执行 solo 与 directed，一共 4 次；失败保留，不重试。

| 任务 / 条件 | 结果 | 秒 | 已报告总 token | 子任务完成/启动 | 协调调用 | status 调用 |
|---|---:|---:|---:|---:|---:|---:|
| directed-csv-ledger-solo | 失败 | 209.8 | 22,538 | 0/0 | 0 | 0 |
| directed-csv-ledger-directed | 通过 | 507.7 | 331,598 | 1/2 | 13 | 11 |
| directed-access-report-directed | 通过 | 133.5 | 217,013 | 2/2 | 5 | 3 |
| directed-access-report-solo | 通过 | 85.2 | 98,163 | 0/0 | 0 | 0 |

## 观察结果

- directed 两题均通过独立验收；启动的 4 个子任务中 **3 个完成**。旧版本同一补充批次为 **1/4 完成**。修复后 access-report 的两个子任务都完成，旧版本两个都被父任务取消。
- directed 的 `worker_status` 调用从旧批次 **16 次降至 14 次**，协调调用从 **22 次降至 18 次**。本次请求实际使用了 `wait_seconds=10/20/30`，证明模型能够发现并使用新字段。
- 整体效率没有改善：修复批次 directed 两题合计 **641.1 秒、548,611 token**；旧批次为 **506.9 秒、487,097 token**。csv-ledger 仍有一个子任务达到 `step_limit`，说明等待接口没有解决子任务预算和任务拆分质量问题。
- 修复批次的 csv-ledger solo 因一次 provider error 失败（4 次 HTTP 尝试中 1 次没有 usage）；没有把它重跑后再计入结果。access-report solo 通过。

因此本次修复可以写成**委派可靠性的小幅改进**：在这两道题中，子任务正常完成数由 **1/4 提升至 3/4**，状态查询由 **16 降至 14**；不能写成并行提速或成本下降。由于样本只有两道题、每条件一次，且 csv-ledger 仍触发步数上限，Worker 暂不作为简历主亮点。

完整原始请求、工作区、子任务 trace 和评分保存在被 Git 忽略的 `artifacts/private/worker-repair-20260924.tar.gz`；紧凑结果在 [worker-repair-20260924.json](worker-repair-20260924.json)。原始 Worker 评测对照见 [主报告](REPORT.md)。
