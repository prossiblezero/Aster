# 核心专项评测

这里维护可复用执行代码与方法说明。基础任务运行器在 [`scripts/`](../scripts/)，任务、fixture、独立检查和来源在 [`datasets/`](../datasets/)。完整命令见[评测指南](../docs/benchmark.md)，已发布报告与原始数据见[指标证据](../docs/evaluation-results.md)。

| 入口 | 内容 | 模型调用 |
| --- | --- | --- |
| [`subagents/`](subagents/README.md) | 固定十题、源码证据评分、Solo/委派对照、全队记账 | fetch/preflight 不调用，run 调用 |
| [`memory/`](memory/README.md) | 十个跨会话知识复用任务与前台/后台成本 | run 调用 |
| [`swebench/`](swebench/README.md) | 固定 Verified 子集、标准预测导出与官方评分指引 | Aster 生成候选时调用 |

历史结果通过[固定提交的归档链接](../docs/evaluation-results.md)公开，包含报告、逐题数据和对应实验材料。新的输出保存在 Git 忽略的 `artifacts/` 或专项结果目录；这里的执行器使用仓库声明的环境和输入。
