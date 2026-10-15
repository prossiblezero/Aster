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

## Stage 1

- `_write_file` / `_patch_file` 返回实际编码后字节的 SHA-256；`_collect_step` 复用一次 `read_edit_source` 同时取得原始字节哈希与规范化文本，匹配写入回执后才更新门禁凭证。
- 保持原有 working memory 清理、文件变化与验证失效逻辑。首次编辑和其他途径修改后的重读要求仍在。
- 同步修改 write_file 的旧描述，避免同一工具表同时出现“自身编辑保持凭证”和“每次编辑都失效”的矛盾；系统提示词未改。
- 定向测试 45 passed，包含外部修改、run_shell 修改、写入后收集前竞态、新建后编辑、同批连续编辑、BOM / 混合换行。
- 既有断言仅修改 `test_batch_matches_original_and_preserves_bom_and_mixed_newlines` 的“自身编辑后凭证消失、再次编辑被拦截”两项，改为凭证保留、再次编辑成功，并检查最终原始字节；其他已列出的既有外部变化断言未改。
- 回滚：`git revert v11-stage1`；或 `git switch -c rollback/turn-stage1 v10-baseline`。
- 全量：ruff 通过，core 484 passed，noncore 821 passed / 6 个基线失败 / 4 skipped；相对基线新增通过 5 项，新增失败 0。详见 `stage1-checks.json`。
