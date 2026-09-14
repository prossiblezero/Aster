# Aster

安全边界清晰、证据驱动的轻量本地 coding-agent Harness。

实现与验收状态见 [实施记录](docs/implementation.md)。

评测数据包含 193 条独立任务及 12 组上下文配置，见 [任务手册](datasets/benchmark.md) 和 [评测方法与结果](docs/benchmark.md)。

```bash
conda activate aster
pip install -e '.[dev]'
python -m pytest -q
python scripts/benchmark.py --task debug-and-fix
```

全量确定性检查：`python scripts/benchmark.py --concurrency 2`。固定真实模型对照：`python scripts/benchmark.py --real --selection real_subset.json --concurrency 2`。失败结果也会保留在报告中。
