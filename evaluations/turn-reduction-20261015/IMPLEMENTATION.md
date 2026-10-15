# v11 实施与验收记录

执行日期：2026-10-16。方案：`docs/turn-reduction-plan.md`。仅本地提交，不调用付费模型。

## Stage 0

- 分支 `perf/turn-reduction`；现有实现基线 `ad63d26`，文档基线 `f0c6365`，标签 `v10-baseline`。
- 原有文档归档触发 5 处固定 dummy key 扫描：两个历史 `aster_episode.py` 和 `general-coding-optimized-v2-20261011/source.patch`。仅拆成相邻字符串字面量，值不变；这些文件的字节哈希因此改变，原评测数据未改。
- ruff 通过；core 484 passed；noncore 816 passed、6 failed、4 skipped。日志保存为本目录 `baseline-{ruff,core,noncore}.log`（按仓库规则忽略日志文件）。
- 已有失败：`test_context_archives.py::test_latest_parallel_batch_fits_one_budget_with_all_results` 两种协议；`test_context_policy_input.py::test_opted_in_worker_fits_small_context` 两种协议 × 两种小窗口。后续仅比较新增失败，不改变这些断言。
- `turn_metrics.py` 已在 v10 原始归档上复算：358 轮，114 纯探索，39 次编辑后重读，27 纯重读轮，11 次读取门禁，102 次搜索，21 纯搜索/列目录轮，60 带失败轮，10 次完成门禁。完整逐题数据：`baseline-turns.json`。
- 纯重读轮按轮开始时的编辑集合判定；同轮对一个刚编辑文件读两个范围仍只算一轮。指标使用最后一个前台请求的 input，保持历史定义，不声称覆盖压缩掉的历史和最后响应。
- 回到基线：从 `v10-baseline` 新建分支；不改写已有提交或标签。
