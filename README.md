# Aster

面向本地代码仓库的轻量 Coding Agent Harness。一个 Runtime 统一模型、工具、权限、工作记忆、恢复与交互，也供外部策略做 rollout；尚未实现 SFT 或 GRPO。

[分阶段学习](docs/learning/README.md) · [配置说明](docs/configuration.md) · [修复验收](docs/repair-results.md) · [评测与比较](docs/benchmark.md) · [简历写法](docs/resume-draft.md)

```bash
conda activate aster
pip install -e '.[dev]'
python -m pytest -q
aster --cwd examples/demo '查看仓库并说明入口'
aster --tui --cwd examples/demo
```

模型连接从项目 `.env` 的 `ASTER_MODEL`、`ASTER_BASE_URL`、`ASTER_API_KEY` 读取，默认 Responses 协议。审批界面需明确批准，Escape 拒绝；Ctrl+C 取消当前任务。

任务内容在 [datasets](datasets/README.md)：193 条主任务、69 个机制场景、12 组上下文配置，题目数与运行条件数分开统计。

```bash
python scripts/benchmark.py --concurrency 4
python scripts/benchmark_mechanisms.py
python scripts/benchmark.py --real --selection real_subset.json --concurrency 4
python scripts/compare_benchmarks.py
```

报告按任务类型展示正确性、工具调用、token 和失败原因；脚本回放不冒充模型成绩，已有评测题及轨迹不进入后续训练集。
