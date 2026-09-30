# SymPy-13878 CDF 委派与补充诊断审计

本审计覆盖 `holdout-sympy__sympy-13878` 的 6 个付费 episode，以及冻结的 12 个分布、interior/boundary 点补充诊断。原独立评分为 Solo 1/2、Current 1/2、Worker 0/2；正确正常交付为 0/2、1/2、0/2。CDF 原评分只包含 Arcsin 加既有回归项，不能解释为十二个分布全部通过。

新版 Worker repeat-01 没有启动 child；repeat-02 启动 1 个 child，child 以 `step_limit` 返回部分调查，未修改文件、未完成验证。父任务收到 `todo_id=1` 的部分交付后修改 `crv_types.py`，运行 focused tests 时仍出现 `test_arcsin`/既有连续分布失败，独立评分也未通过。Current 两次都启动了 child，均部分交付；其中 repeat-01 child 修改了若干 `_cdf`，但未完成测试，父任务最终因团队 token 停止；repeat-02 child 只提供公式调查，父任务独立完成主要修改并通过原评分。没有证据可以把 Current 的一次通过或任何 CDF 差异单独归因于 child。

冻结公共诊断的两次合计如下：

| 组 | Solo | Current | Worker |
| --- | ---: | ---: | ---: |
| printer 6 个样例：verified / wrong / unknown | 0 / 6 / 0 | 0 / 6 / 0 | 2 / 4 / 0 |
| CDF 144 个固定点：verified / wrong / unknown | 127 / 12 / 5 | 127 / 11 / 6 | 130 / 10 / 4 |

Worker 的两个 printer verified 都是 pretty；str 和 LaTeX 仍 wrong。CDF 的 verified 点数略高，但 Worker 仍在 Erlang、StudentT、Frechet/UniformSum 边界或超时上失败。该诊断没有模型调用，且输入、超时、独立数学参考都在 `diagnostics/freeze.json` 中锁定；它是需求级辅助信号，不能替换付费任务评分，也不能证明点数差异来自委派。

证据位于：

- 付费结果：`artifacts/private/worker-owned-20260930/repeat-{01,02}/runs/holdout-sympy__sympy-13878--*/`
- 补充汇总：`artifacts/private/worker-owned-20260930/diagnostics/summary.json`
- 冻结规则：[SUPPLEMENTARY-PROTOCOL.md](SUPPLEMENTARY-PROTOCOL.md)
