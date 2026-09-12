# Aster

安全边界清晰、证据驱动的轻量本地 coding-agent Harness。

实现与验收状态见 [实施记录](docs/implementation.md)。

模块级量化评测见 [Benchmark](docs/benchmark.md)，运行 `PYTHONPATH=src:scripts python scripts/benchmark_modules.py` 可复现。

```bash
conda activate aster
pip install -e '.[dev]'
```
