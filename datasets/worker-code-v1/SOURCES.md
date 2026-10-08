# Worker Code Benchmark v1 来源记录

## 主要来源

- 数据集：[`SWE-bench/SWE-bench_Verified`](https://huggingface.co/datasets/SWE-bench/SWE-bench_Verified)，`test` split，500 条实例。
- benchmark/harness：[`SWE-bench/SWE-bench`](https://github.com/SWE-bench/SWE-bench)，固定 revision `02e7a74ffd0b707aab73d203fe87bdc7c76afc8e`，仓库 harness 许可证为 MIT。
- Hugging Face 行数据接口：`https://datasets-server.huggingface.co/rows?dataset=SWE-bench%2FSWE-bench_Verified&config=default&split=test&offset=<n>&length=100`。
- 生成时间：2026-09-27；固定选择哈希见 [`manifest.json`](manifest.json)。

SWE-bench 的 MIT 许可证只覆盖其 benchmark/harness 仓库。题目中的代码、issue、测试和运行环境属于各自上游仓库；清单逐条记录了来源仓库许可证，下载器必须保留对应 `LICENSE`/`NOTICE`。本项目不把所有题目声明为 MIT，也不把上游仓库复制进本目录。

## 选入的上游仓库

| 仓库 | 清单中的许可证说明 |
|---|---|
| `django/django` | BSD-3-Clause（以每个 `base_commit` 的 LICENSE 为准） |
| `astropy/astropy` | BSD-3-Clause（以每个 `base_commit` 的 LICENSE.rst 为准） |
| `sphinx-doc/sphinx` | BSD-2-Clause（以每个 `base_commit` 的 LICENSE.rst 为准） |
| `sympy/sympy` | BSD-3-Clause（以每个 `base_commit` 的 LICENSE 为准） |
| `pytest-dev/pytest` | MIT（以每个 `base_commit` 的 LICENSE 为准） |
| `pydata/xarray` | Apache-2.0（以每个 `base_commit` 的 LICENSE 为准） |

## 数据隔离

公开清单不保存 gold patch、`test_patch` 或完整仓库。它们的 SHA-256 仅用于运行前后核对，模型可见 prompt 只包含 issue 描述和必要的来源 revision。运行时取得的 gold/test artifacts 应放在被版本控制忽略的 grader 缓存中，隐藏测试只在候选代码完成后注入评分副本。

当前版本没有运行 SWE-bench 官方 Docker harness，因此不能报告 SWE-bench 官方成绩。后续实验只报告本清单固定 20 题及其明确的 solo/worker 对照结果；下载、解压或 base revision 校验失败的题必须记为环境失败，不能选择性移除。

## 其他 benchmark 的取舍

HumanEval、LiveCodeBench 和 RepoBench 主要是函数级、竞赛题或代码补全，缺少真实仓库 issue 到隐藏回归测试的链路，保留为其他能力的对照，不混入 Worker 主集。Terminal-Bench 需要 Harbor/Docker 和系统级环境，暂不作为轻量 Aster 首批任务。SWE-smith 与 SWT-Bench 的规模和 Docker 依赖也留作后续扩展，不与本 20 题混合统计。
