# any / all 实现与实验验证

## 实现范围

相对于本轮开始时的 55 文件、12,014 行源快照，修改四个生产文件，增加 22 行、删除 12 行，净增 10 行；文件数仍为 55，总计 12,024 行。差异见 [candidate.patch](candidate.patch)。没有新增运行依赖、工具、持久化字段、用户配置或调度器。

`worker_status.wait_for` 由 Pydantic 限制为 any/all，默认 any；Workflow 将其传到既有 WorkerManager，列表等待按参数使用 FIRST_COMPLETED 或 ALL_COMPLETED。all 忽略已有未读结果的提前返回条件。其他完成、交付消费、编辑与最终测试门槛沿用原路径。

## 本地检查

- 等待边界聚焦检查：12 passed，3.70 秒。覆盖默认 any、all 等齐、已有未读结果、恢复和不可用记录、零秒快照、共同期限、去重和缺失 ID、超时/取消不取消子任务、未知选项拒绝。
- 仓库 `tests/`：1,084 passed、4 skipped，126.71 秒。
- 教学站点重新生成，`learn_web/tests/`：25 passed，1.67 秒；与核心测试合计 1,109 passed、4 skipped，分两条命令执行。
- 源码、测试及新评测驱动 Ruff 通过。
- 隔离构建 wheel，安装至单独目录后从 `/tmp` 启动同一测试进程，先断言 Aster 导入路径再运行 Worker/交付/工具契约测试：174 passed，35.28 秒。55 个生产文件与 checkout 及冻结候选完全一致，见 [WHEEL-VERIFICATION.json](WHEEL-VERIFICATION.json)。依赖仍来自同一 Conda 环境，不视为跨平台验证。

初次聚焦运行有两个新增测试在等待工具尚未返回时调用 Final，被既有 `runtime_busy` 保护拒绝。调整测试调用顺序后通过；没有修改生产并发保护。

## 实验约束

模型任务调用前冻结 [协议](PROTOCOL.md)、驱动、源码、评分索引修订、配置身份、端点哈希、Python/依赖摘要和顺序。修订先通过 48 个可执行事实、42 个负例、40 个新增嵌套符号引用正例，保留全部原索引范围；没有改写历史评分。

健康请求单独成功，报告 4,396 token。正式批次使用同一候选源码；仅双子任务首次状态调用的 wait_for 不同，后续行为由模型控制。任务使用独立只读工作区，源码、工作区和 usage 每回合核对；完整轨迹和请求账本保存在忽略目录 `artifacts/private/subagent-wait-modes-20261002`。

## 正式批次与最终核对

- 36 个预定 task/arm/repeat 组合全部存在，原始请求启动时间逐项符合冻结顺序，没有缺失、替换或额外重跑。公开 results.json 与各回合原始结果逐项一致。
- 35 个父运行完成，一个 Solo 运行在 SSL 连接层失败。57 个子运行中 56 个完成，一个因 team_token_limit 停止；它属于 any 组的额外续跑，保留了部分交付，不算完成。
- any 的 24 次初始委派另有 9 次续跑，all 只有 24 次初始委派。通过真实 run_continued 事件核对，详情在 diagnostics.json 的 continuations 中。
- 36 个工作区和冻结源文件都保持完整，运行 usage 与请求账本逐项相等。最终 checkout 的全部 55 个生产文件仍等于冻结候选；驱动、协议、评分数据和依赖文件哈希与冻结值一致。此前 live 实验的冻结依赖也未改变。
- 340 次模型请求，记录到 340 次 HTTP 诊断（339 成功、1 传输失败）；另一个 SSLError 请求没有 HTTP 诊断或 usage。传输失败约 120.003 秒，发生于 any 第一遍 Django 调查，原有重试成功。正式已报告 token 合计 3,902,561，不能把两处未知用量算成零。
- 每条持久化 trace 的序号、previous 及事件 hash 链全部验证；轨迹、launch、请求账本和原始结果的文件摘要保存于 [TRACE-HASHES.json](TRACE-HASHES.json)。
- 本批评分索引修正在模型调用之前冻结。后续 diagnose 只分析原始结果，不改写答案或评分；把纯行为、引用和 JSON 格式问题分开列出。只有已完成的 11 对 Solo 对照进入补充耗时敏感性分析，有报告 token 的 11 对进入 token 配对；主成绩仍包含 SSL 失败。

报告和文档已同步，生产与新评测代码 Ruff、差异空白及本地链接检查通过。分析脚本和报告数据的摘要见 [ANALYSIS-HASHES.json](ANALYSIS-HASHES.json)。

```bash
python -m pytest -q tests
python -m pytest -q learn_web/tests
python -m ruff check src tests evaluations/subagent-wait-modes-20261002
python evaluations/subagent-wait-modes-20261002/evaluate.py summarize
python evaluations/subagent-wait-modes-20261002/diagnose.py
```

`.env` 未改写，原始凭据未进入公开评测文件；历史批次、首次失败和原有未提交修改均保留。wheel 及原始检查日志位于忽略目录 `artifacts/private/subagent-wait-modes-20261002`。
