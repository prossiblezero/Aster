# Aster

面向本地代码仓库的轻量 Coding Agent Harness。一个 Runtime 统一模型、工具、权限、工作记忆、恢复与交互，也供外部策略做 rollout；尚未实现 SFT 或 GRPO。

[分阶段学习](docs/learning/README.md) · [教学网站](learn_web/README.md) · [配置说明](docs/configuration.md) · [修复验收](docs/repair-results.md) · [评测与比较](docs/benchmark.md) · [简历写法](docs/resume-draft.md)

最近实现：[长任务分批记录](docs/long-task-implementation.md)，包括批量补丁、命令输出回读、模型配置与会话续聊、Responses 流式、只读工具并行、受控依赖复用及 [Skills 包](docs/skills.md)。

[v0.2 工程收尾与真实任务验收](evaluations/v0.2-release/REPORT.md)：补齐模型响应诊断、有界重试、CLI 流式进度、安全恢复和执行环境契约。新会话默认单 Agent，Memory、自动维护及 Worker 默认关闭，可显式开启；九个核心工具不变。真实任务的首测失败、修复、独立验收和剩余门槛均保留，不把可选机制存在等同于收益。

[实验数据工具修复复测](evaluations/lab-followup-20260919/REPORT.md)：补齐依赖丢失和正式测试入口的反馈。隔离首测失败及有限返修全部保留；最终[可交互CSV→Excel产品](exam_project/lab_data/README.md)通过24项独立验收和5项正式自测，不能解释为首次自主成功。

教学网站、专用运行脚本、测试和历史证据集中在 `learn_web/`；可运行的小项目统一放在 [exam_project/](exam_project/README.md)，评测协议、驱动、报告与历史证据放在 [evaluations/](evaluations/README.md)。课程正文仍以 `docs/learning/` 为来源。

独立实测：[流式](evaluations/archive/stream_comparison/report.md) · [只读并行](evaluations/archive/parallel_comparison/report.md) · [缓存](evaluations/archive/cache_comparison/report.md) · [Memory](evaluations/archive/memory_followup/report.md) · [Skills](evaluations/archive/skills_comparison/report.md) · [会话恢复](evaluations/archive/session_recovery/report.md)。后训练仅完成[方案与数据契约](docs/post-training-design.md)，尚无训练器或训练效果。

```bash
conda activate aster
pip install -e '.[dev]'
python -m pytest -q
# 无需 API Key，先体验离线循环：
python docs/learning/run_examples.py 00
# 真实模型与 TUI 的配置、演示目录见学习第 00 章。
```

模型连接从项目 `.env` 的 `ASTER_MODEL`、`ASTER_BASE_URL`、`ASTER_API_KEY` 读取，默认 Responses 协议。审批界面需明确批准，Escape 拒绝；Ctrl+C 取消当前任务。

任务内容在 [datasets](datasets/README.md)：193 条基础主任务、85 个机制场景；另有 [上下文与恢复专项](datasets/robustness.md)，用真实模型验证历史裁剪与停滞纠偏。题目、故障场景、预算配置和重复次数分别统计。

```bash
python scripts/benchmark.py --concurrency 4
python scripts/benchmark_mechanisms.py
python scripts/benchmark.py --real --selection real_subset.json --concurrency 4
python scripts/compare_benchmarks.py
python scripts/benchmark_robustness.py --real --concurrency 8 --output artifacts/benchmark-v5-robustness
```

报告按任务类型展示正确性、工具调用、token 和失败原因；脚本回放不冒充模型成绩，已有评测题及轨迹不进入后续训练集。
