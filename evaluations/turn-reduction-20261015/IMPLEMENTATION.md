# v11 实施与验收记录

执行日期：2026-10-16。方案：`docs/turn-reduction-plan.md`。Stage 0–4 未调用付费模型；用户确认后执行的第一轮付费结果见下方 Stage 5。提交仅限本地。

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

## Stage 2

- `Search` 增加默认关闭的 `regex`，pattern 上限 300；`_search` 复用原分页，正则按 `IGNORECASE` 匹配每行前 2000 字符，编译错误返回 `invalid_regex`；字面匹配与 300 字符预览保持原行为。
- 定向测试 14 passed；已有分页断言全部未改，新增交替匹配、默认字面量、行截断和非法模式验证。
- 回滚：`git revert v11-stage2`；或 `git switch -c rollback/turn-stage2 v11-stage1`。
- 初版沿用冗长的搜索说明，新增 3 个 4096 上下文测试失败。改为方案指定的精简工具描述，分页语义仍由参数定义和返回字段表达；定向复核已恢复到基线失败集合。记录见 `stage2-before-trim-checks.json`，未修改测试断言或上下文策略。
- 最终全量：ruff 通过，core 484 passed，noncore 822 passed / 6 个基线失败 / 4 skipped；相对基线新增通过 6 项，新增失败 0。详见 `stage2-checks.json`。

## Stage 3

- `run_tests` 包装现有 `_run_tests`：新失败在同一工具调用内，以相同命令/超时，在临时工作区恢复 `_initial_text` 后复跑。恢复改动/删除文本、移除新文本，其他文件保留；内联检查不复跑，已检查 ID 不重复复跑。
- 记录 failed / passed / missing。基线执行不可用不写证据；基线摘要被长输出裁剪时，`Verifier.observe` 在预览末尾补回，避免模型看不到操作提示。
- `RunState` 保存基线结果与显式声明；仅接受有 failed 基线证据的声明。保留工具真实 tests_failed 结果；仅当该次完整测试的失败全部已声明、且没有执行错误或文件/环境漂移时，任务验证可通过。最终 details 和 workflow_state 明示未修复的既有失败。
- 检查点/恢复沿用现有 RunState 序列化；任务续写或采纳外部修改引起基线更新时，清空旧证据与豁免，并把原豁免失败重新纳入门禁。
- 系统提示只替换方案指定的一句；`completion_feedback`、最终验收顺序、缓存策略、Worker 和两组保护测试均未改变。
- 新 schema 一度增加小窗口失败；仅压缩 run_tests 工具与 code 参数中重复的说明，保留执行器约束，不改预算策略或既有断言。最终仅剩原基线失败。
- 新增 9 项核心测试全部通过；原正式测试路径定向检查 46 passed / 4 skipped（加后续新增竞态测试单独验证）；最终全量 ruff 通过，core 493 passed，noncore 822 passed / 6 个基线失败 / 4 skipped。相对 Stage 0 共新增通过 15 项、新增失败 0。
- 回滚：`git revert v11-stage3`；或 `git switch -c rollback/turn-stage3 v11-stage2`。

## Stage 4

- 冻结协议 `PROTOCOL.md`、执行器 `evaluate.py`、缓存计量 `metrics.py`、逐轮审计 `audit.py`。CLI 支持 `--round`、`--tasks`、`--attempt`；私有原始目录分轮/尝试隔离并加运行锁，拒绝覆盖未分类的已启动尝试。
- 第一轮运行时从 `v11-stage2` / `b2b195c` 导出；第二轮从 `v11-stage3` / `8290388` 导出。`manifest-round1.json`、`manifest-round2.json` 保存完整 SHA-256。已验证第一轮没有 Stage 3 字段、源码与标签一致。
- 离线检查复现 v10 全部轮次与缓存指标，包括 378 次有用量前台请求、加权成本 3.524968；验证 8/18 边界、条件补两次规则、禁止重复任务/覆盖原始数据、声明证据关联。记录 `offline-checks.json`。
- Docker 可用，20 道题涉及的 18 个镜像全部存在，控制环境包含 SWE-bench；`environment-readiness.json`。未创建评测任务容器、未发起模型请求。
- 完整前缀、轨迹来源、外部修改门禁等保护范围复核见 `scope-check.json`；AST 核对系统提示只改了授权的那一句。
- 两轮各 32 次任务尝试，条件补考至多再加 2 次；第二轮必须先通过第一轮且另获确认。只用每轮 attempt 1 的 old20 比成本；声明需要逐条人工核对任务相关性，不由基线失败自动判定豁免合理。
- 本轮停止于方案指定的付费执行确认点，没有新 benchmark 成绩，也不据局部机制测试宣称 token / 费用已经降低。
- 回滚评测准备提交：`git revert v11-stage4`；或 `git switch -c rollback/turn-stage4 v11-stage3`。私有冻结快照保留供复现。

## Stage 5：第一轮付费评测

- 用户明确要求“跑付费评测吧”后，使用冻结 `v11-stage2` / `b2b195c`，依次完成 old20 首次 20 题、六道波动题的第二和第三次尝试，共 32/32 次；全部独立评分，无稳定题补测，无择优合并。
- 首次独立通过 14/20，正常交付且通过 12/20；历史稳定通过组 10/10，历史稳定失败组 0/4，波动组累计 12/18。质量门槛通过，源码/请求/轨迹来源审计错误为 0。
- 首次轮次 358 → 304，前台有用量请求 378 → 323，读取门禁阻断 11 → 0，纯重读轮数 27 → 8，缓存命中率 87.74%，严格前缀改写比例 3/303。纯重读要求 ≤5，故第一轮整体验收未通过。
- 加权前台成本 3.524968 → 2.966459（下降 15.84%），其中 84.62% 的净节省来自解释器首次提前停止。该题独立测试通过，但因 `/app/interp.py` 访问被拒绝而以 `stall_recovery_exhausted` 停止。排除该题后，其余 19 题成本只下降 2.93%；不据此宣称稳定的整体优化收益。
- 全部 32 次尝试共 573 次请求、13,034,745 token；含后台记忆的加权已观测费用 5.1897425（非账单）。4 次请求缺用量，全部保留异常且不估填。
- 已保存 [完整报告](REPORT-round1.md)、[冻结审计](audit-round1.json)、[费用与历史对比](comparison-round1.json)、[执行与清理记录](execution-round1.json)，逐次统计位于 `round1/attempt{1,2,3}/`。只重跑 Aster；pi、Codex 使用相同 old20 的历史结果。
- 按协议第一轮未通过即停止，未启动第二轮、未自动回滚 Stage 1/2；是否回到 `v10-baseline` 由用户决定。Stage 5 未达到整体验收条件，未打完成标签。生产代码、冻结协议、执行器、指标定义和私有原始档案均未改写。

## Stage 3 撤销

- 2026-10-17 按用户指定的 `docs/v11-closeout-plan.md` 收尾。第二轮 34 次独立评分及 7 项人工核对原样保存于提交 `753bcfada7aae9e633d388784a491f0616496da3`。
- 撤销提交：`30bd0b3`。仅撤销 `8290388` 的源码和测试，保留本实施记录、`stage3-checks.json` 及全部评测证据；原实现仍保存在 `v11-stage3`。
- 撤销原因：第二轮首次 20 题有 46 次请求异常，4 题以 `provider_error` 提前停止；剔除这 4 题后轮数 252 → 230、请求数 279 → 281，未证明降本。Requests-2931 带失败轮数从第一轮 3 增至 9；pytest-10356 的任务相关测试被运行时接受为 pre-existing，用户人工核对明确不予豁免。基线失败不能单独证明任务无关。
- 第二轮质量门槛通过，但 Requests 带失败轮数、声明相关性、归档证据一致性三项未通过；本次根据收尾方案主动撤销 Stage 3，不改写历史验收结论。
- Stage A 验收：`git diff v11-stage2 HEAD -- src tests` 无输出；Ruff 通过，core 484 passed，noncore 822 passed / 6 个原有失败 / 4 skipped，失败 ID 集合与 `stage2-noncore.log` 完全一致。日志为 `closeout-stageA-{ruff,core,noncore}.log`，按仓库规则忽略。
- 验收标签 `v11-final` 标记保留 Stage 1、2 的版本。需要恢复 Stage 3 时可 `git revert 30bd0b3`，或从 `v11-stage3` 新建分支；不改写现有提交和标签。
