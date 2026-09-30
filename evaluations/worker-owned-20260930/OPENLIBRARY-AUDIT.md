# OpenLibrary authors 委派审计

本审计覆盖 `swe-pro-openlibrary-authors` 的 6 个 episode（两次重复、三臂）。独立评分为 Solo 1/2、Current 0/2、Worker 0/2；正确正常交付相同。Solo 的 repeat-02 通过，另外五条分别遗漏作者匹配规则或在团队预算边界停止。

新版 Worker 两次都没有启动子任务，因此这道题没有“新版子 Agent 解决问题”的证据。Current 两次都启动了只读 Explore，但两个 child 都没有完成；报告进入父请求后，父任务仍重读来源并自行实现。repeat-01 child 提供了 `find_author`、alternate name、surname 和日期匹配的代码位置与测试建议，但交付是 `partial`、无文件修改；repeat-02 child 修改了 `load_book.py` 后以 `turn_limit` 停止，父任务仍需自行修正 `mock_infobase.py` 和日期/通配符语义。

独立评分的差异也没有形成正向结论：Solo repeat-01 只通过 3/4 个 fail-to-pass 测试，repeat-02 通过全部 4 个；新版 Worker repeat-01 通过 3/4、repeat-02 通过 3/4。新版 Worker 的两条运行合计报告 token 1,529,524，Solo 为 1,882,828，但新版没有启动 child，不能把差异归因于委派；Current 的两次 child 合计增加 3 次协调调用和 2 个未完成子任务，仍未通过独立评分。

证据位于：

- `artifacts/private/worker-owned-20260930/repeat-{01,02}/runs/swe-pro-openlibrary-authors--{solo,current,worker}/result.json`
- 同目录 `grading/grade.json`、`grading/tests.log`、`request-ledger.json`
- Current child 的 `.aster/runs/*/handoff.json` 及父请求目录；新版 Worker 没有 child run

结论是“调查交付确实可送达”，不是“调查提高了任务正确性或降低了成功成本”。
