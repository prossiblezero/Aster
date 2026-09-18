# 评测与历史归档

这里存放 Aster 的实验协议、驱动、评分器、对照结果和运行证据。可运行的小项目放在 [exam_project](../exam_project/README.md)，教学网站放在 `learn_web/`。

## 历史归档

`archive/` 是只读历史材料。2026-09-18 将原 `exam_project/` 下八个实验目录整体迁入，保留全部 7,092 个文件，包括被 Git 忽略的本地证据与冻结源码；迁移前后逐文件摘要一致。详见 [迁移清单](archive-migration-20260918.json)。被忽略的本地证据仍不随普通 Git 提交上传。

原驱动、需求、评分器、报告与结果没有重写或重新计算实验身份。历史材料中的 `exam_project/...` 命令、绝对路径、冻结源码根和部分向外相对链接反映当时的位置，**不能直接当作当前启动命令**。移动位置不代表完成了一次新的评测；如需重跑，应在新的实验目录另行冻结协议和身份，输出新结果。不要在归档里覆盖候选、改写来源哈希或追加模型任务。

| 历史实验 | 报告 / 说明 | 独立产品 |
|---|---|---|
| Markdown CLI 与早期 Memory 对照 | [mdcheck_memory](archive/mdcheck_memory/report.md) | [mdcheck](../exam_project/mdcheck/README.md) |
| 空目录编码冒烟 | [long_task_smoke](archive/long_task_smoke/README.md) | [csvtotals](../exam_project/csvtotals/README.md) |
| 流式对照 | [stream_comparison](archive/stream_comparison/report.md) | [linecounts](../exam_project/linecounts/README.md) |
| 只读工具并行 | [parallel_comparison](archive/parallel_comparison/report.md) | [tagmerge](../exam_project/tagmerge/README.md) |
| 缓存短请求探针 | [cache_comparison](archive/cache_comparison/report.md) | 无，只有探针 |
| Memory 自然触发 | [memory_followup](archive/memory_followup/report.md) | [notetally](../exam_project/notetally/README.md) |
| Skills 发现与加载 | [skills_comparison](archive/skills_comparison/report.md) | [eventcount](../exam_project/eventcount/README.md) |
| 中断恢复与 profile 切换 | [session_recovery](archive/session_recovery/report.md) | [uniqwords](../exam_project/uniqwords/README.md) |

产品副本优先选择 off、nonstream 或 serial 条件；uniqwords 来自完成的第一阶段，不能把它解释为第二阶段 profile 切换成功。复制的实现、测试和示例保持原样，每个产品增加 `ORIGIN.md` 说明来源。

## 新实验

[v0.2 工程收尾验收](v0.2-release/REPORT.md)：公开 CLI 从空目录开发实验数据工具和复习安排网页，真实 TUI 维护课业清单并验证取消、恢复和模型切换。保留框架干预、产品返修、独立反例及全部已知成本；只有验收合格的产品进入 `exam_project/`。

[2026-09-18 真实课业清单与机制复测](benefit-20260918/REPORT.md)：缓存阳性校准、网关观测、实际浏览器产品、Skills维护任务及Memory跨会话资料问答。协议、失败、评分器修订和所有条件单独保存；可运行成果见`exam_project/campus_planner`。

每轮实验使用独立目录，明确产品版本、任务需求、评分标准、对照条件和预算；用 aster 虚拟环境执行。机制是否触发、交付质量、性能差异分别记录，不能把单个案例的耗时或 token 差直接当作功能收益。

历史任务已经参与工程开发和诊断，只能作为审计材料，不能重新标成未见训练测试集。
