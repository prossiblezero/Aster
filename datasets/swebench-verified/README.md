# SWE-bench Verified 十题清单

来源：[SWE-bench/SWE-bench_Verified](https://huggingface.co/datasets/SWE-bench/SWE-bench_Verified)，`test` split。`selection.json` 记录固定题号与上游代码版本，包含问题描述和 gold/test patch 哈希，不包含 gold patch、模型预测或运行结果。

选择来自 2026-10-08 本地实验：3 simple、5 medium、2 hard（Aster 的难度映射），四个上游仓库，每仓库最多三题。具体定义、先前已用题排除规则及环境筛选保存在清单中。环境筛选解释历史选题过程，不替代官方 Docker 环境。

任务是参与过开发的固定子集，不是从未看过的测试集，也不代表完整 Verified。数据卡、上游仓库与官方 harness 各自的许可仍适用；不对第三方问题或代码重新授权。

运行与预测导出见 [SWE-bench 指南](../../evaluations/swebench/README.md)。
