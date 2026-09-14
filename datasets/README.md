# Aster evaluation datasets

评测任务与执行器分离。JSON 文件只描述 fixture、用户任务、难度、期望结果和步数上限；`scripts/` 负责加载它们、运行 Runtime，并生成报告。

- `agent_tasks.json`：真实模型 smoke task bank。
- `module_cases.json`：确定性模块评测的规模与对照说明。

新增任务时保持独立 fixture、可自动验证、明确难度和失败判定，避免把任务内容硬编码进评测逻辑。
