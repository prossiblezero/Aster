# 评测指南

工程回归、参考动作回放、机制检查和真实模型任务回答不同问题。先选择需要验证的层次，再运行相应入口。所有命令从仓库根目录、安装 `.[dev,docs]` 后执行；需要命令隔离的部分要求 Linux Landlock/libseccomp。

## 不调用模型的检查

```bash
python -m pytest -q
python scripts/benchmark.py --task debug-and-fix --env-file /dev/null --output artifacts/quickstart
python scripts/benchmark_mechanisms.py --output artifacts/mechanisms
```

第二条将参考动作交给实际 Runtime，检查缺陷初始状态失败、修复后独立评分通过。它验证运行链与评分器，不是模型自主修复成绩。完整参考回放可省略 `--task`；基础任务数据见 [datasets/README.md](../datasets/README.md)。

## 真实模型任务

配置 `.env` 后，先用单题确认服务、预算和沙箱：

```bash
python scripts/benchmark.py --real --task debug-and-fix --env-file .env --output artifacts/real-one
```

固定真实子集：

```bash
python scripts/benchmark.py --real --selection real_subset.json \
  --repeats 1 --concurrency 1 --env-file .env --output artifacts/real-subset
```

这些命令会产生模型费用。执行器保留失败、模型用量和停止原因，分任务族与条件统计。`fixtures/` 给模型作为初始工作区，`checks/` 在运行结束后才加入独立评分副本。参考答案不交给真实模型。

## 专项入口

| 专项 | 数据和入口 |
| --- | --- |
| 上下文与恢复 | `datasets/context_task_cases.json`、`recovery_task_cases.json`；`python scripts/benchmark_robustness.py --help` |
| 子任务证据与委派 | [相关设计沿革](optimization-history.md) |
| 跨会话记忆与维护成本 | [记忆指南](../evaluations/memory/README.md) |
| SWE-bench Verified 子集 | [官方评分路径](../evaluations/swebench/README.md) |
| 多来源 Worker 任务 | [固定来源与材料](../datasets/worker-code-v2/README.md)，环境需按任务自行准备 |

旧 Worker v1、holdout、focused 和 owned 清单作为历史任务定义保留；它们不提供维护者本机环境，不能当作一条命令即可复跑的官方 benchmark。以来源、任务和适配器的公开说明为准。

## 指标与证据

- **任务完成**：符合任务要求的正常结束。代码检查通过但预算耗尽，仍保留未完成状态。
- **独立正确性**：由模型结束后执行的检查判定，不能用模型答案或 Shell 中的 passed 字样替代。
- **机制触发**：确认被测路径实际执行；没有派工的 team 回合不能证明 Worker 改进有效。
- **成本**：全任务树及后台记忆的已报告输入/输出/缓存用量。缺失用量单列，计价未知时不声称账单节省。
- **配对比较**：相同任务、条件与尝试编号对齐；成本在共同成功样本上的比较只作为补充，完整失败样本仍需展示。
- **Pass@k**：k 表示同题独立尝试数量，不能把不同开关或预算拼成重试。

比较器 `scripts/compare_benchmarks.py`、`scripts/compare_context_runs.py` 接收本地生成的报告；具体参数见 `--help`。工程测试使用合成报告验证失败保留、完整配对与变更拒绝，不依赖任何已上传成绩。

## 结果与来源

运行输出默认进入 `artifacts/`。不提交原始模型请求、工作区副本、预测、凭据、轨迹或逐轮成绩。来源、许可与固定版本见 [SOURCES.md](../datasets/SOURCES.md)；测试隔离的边界见[评分边界](grader-boundary.md)。

历史实验参与过开发，不能重新标为未见数据。关键问题、负面结果与设计决定统一记录在[优化历程](optimization-history.md)；该文不是当前版本的新评测。
