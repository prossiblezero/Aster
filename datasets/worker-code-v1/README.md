# Aster Worker Code Benchmark v1

这是 Aster Worker 委派机制的首个公开代码任务评测集。任务来自 **SWE-bench Verified** 的 `test` split，固定到 benchmark 仓库 revision `02e7a74ffd0b707aab73d203fe87bdc7c76afc8e`。每条任务是真实开源仓库中的 issue，模型需要在对应 `base_commit` 上修改代码并通过独立验证。

本目录只保存任务元数据和 issue 描述。完整仓库、gold patch、`test_patch` 和官方评测脚本在运行时按 `repo`、`base_commit` 和 `instance_id` 获取，并通过清单中的 SHA-256 校验。gold patch 与隐藏测试不会进入模型 prompt，也不能从本清单直接还原。

## 任务组成

| 难度 | 数量 | 用途 |
|---|---:|---|
| simple | 6 | 单组件负控制，检验自动委派的协调开销 |
| medium | 10 | 两个以上可独立调查的模块与一个集成边界 |
| hard | 4 | 算法、schema、语法或跨包耦合问题，需要父任务整合 |

难度是 Aster 的评测分层，不是 SWE-bench 官方分数或官方难度字段的改名。分层策略、每条题的选择理由和淘汰记录见 [`selection-review.md`](selection-review.md)。`decomposition` 只给 runner 做任务分配和轨迹审计；不能把 gold patch 推导出的文件列表作为模型提示的一部分。

## 文件

- [`manifest.json`](manifest.json)：版本、来源、6/10/4 比例、运行契约和固定选择哈希。
- [`instances.jsonl`](instances.jsonl)：一行一条任务，含 issue prompt、来源 revision、许可证、grader 哈希和人工分解。
- [`SOURCES.md`](SOURCES.md)：来源、许可证边界、下载方式和不可宣称的结果范围。
- [`selection-review.md`](selection-review.md)：选择标准、协同单元、负控制和排除候选。
- [`checks/validate.py`](checks/validate.py)：不联网的结构和选择哈希校验。

## 运行边界

当前仓库没有把 SWE-bench Docker harness 伪装成本地 runner。后续 runner 必须为 solo 与 capability-worker 使用同一个 `base_commit` 工作区、相同 prompt/预算和相同隐藏验证器；本版每个 `decomposition.units[]` 都是 `read_only`，Worker 只能在隔离 checkout 中读代码并输出结构化证据，由父任务整合后统一修改和验证。没有 Docker/等价隔离环境时，应报告为未运行，不能把下载成功当作任务通过。

首要结果建议同时报告整题 hidden test 通过率、全树 token、wall-clock、工具调用数、`independent_unit_evidence_coverage`、父任务读取重叠率和 handoff 完整度。这个 20 题子集不代表官方 SWE-bench 成绩，也不用于声称 Aster 在全部 SWE-bench 上的泛化能力。

```bash
python datasets/worker-code-v1/checks/validate.py
```
