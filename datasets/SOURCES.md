# Aster benchmark 的来源记录

对外统一使用 **Aster benchmark**。保留以下来源是为了复现和尊重原作者，不表示 Aster 引入了其他项目的运行时代码。

## Harness 与仓库场景

12 条 Harness 用例及 5 条仓库题的初始材料参考用户提供的本地 `pico-v3`：

- 源版本：`8361861e9fe34955fea42f31b7a81e0835c2e02a`。
- 清单：`benchmarks/coding_tasks.json`（12 条）、`benchmarks/long_session_tasks.json`（5 条）。
- 工作区材料：对应的 `tests/fixtures/bench_repo_*`、`long_session_*`。
- Aster 调整：原生结构化工具调用、显式测试工具、真实证据引用、独立验证器、恢复失败后显式开始新运行；日志样例命名为 `error.txt`。
- 本地副本未发现顶层 LICENSE，因此不对这些素材作 MIT 或 Apache 授权声明；来源项目许可状态需在对外分发素材前核实。

这里比较的是两个 JSON 清单中的 17 条任务；pico-v3 另有记忆挑战、dream 场景和单元测试，不能用“193 > 17”宣称 Aster 在所有 Harness 机制上覆盖更多。新题库的大部分新增数量来自函数题，报告必须按任务组阅读。

12 条跨运行记忆任务与上下文压力矩阵为 Aster 组织，具体内容全部列在当前数据文件中。

2026-09 上下文专项的 12 道代码题、1 个不可重读观察控制、历史压力材料及隐藏测试为本项目新编写，见 `context_task_cases.json`、`context_history.json` 和 `checks/context_*.py`。恢复专项在上述已有 5 道仓库题上组织 12 个故障前缀，明确保留 `base_task` 引用，不把故障类型变化称为新独立代码题；16 个新增恢复机制场景及 10 个检测器契约同样为本项目编写。

## HumanEval

- 官方仓库：<https://github.com/openai/human-eval>。
- 读取时版本：`6d43fb980f9fee3c892a914eda09951f772ad10d`。
- 数据：`data/HumanEval.jsonl.gz`，包含 HumanEval/0–163 共 164 题。
- 保留官方 prompt、canonical_solution、test 和 entry_point 的含义；MIT 许可证见 [HumanEval-LICENSE.txt](HumanEval-LICENSE.txt)。
- Aster 将函数补全改成文件工具任务：`solution.py` 的函数抛出 `NotImplementedError`，模型读取并修改后运行公开测试。公开测试仅检查可调用，官方正确性断言只由外部验证器执行。
- 第 33 题的官方测试将 `sort_third` 当作预期结果函数；验证器固定该函数为官方参考实现，与模型提交的函数分开比较。
- 官方 prompt 中的辅助函数在验证器中保持原样，防止测试缺少依赖，也避免模型修改辅助函数来改变预期答案。参考解只用于离线回放验证。

这是文件工具协议下的 HumanEval 使用方式，不能把参考解回放率或 12 题真实子集的得分称作完整 HumanEval 官方成绩。

## SWE-bench

参考官方仓库 <https://github.com/SWE-bench/SWE-bench> 的任务结构和测试思路，读取时版本 `02e7a74ffd0b707aab73d203fe87bdc7c76afc8e`。Worker 首个真实仓库评测集见 [`worker-code-v1/`](worker-code-v1/)，固定使用 `SWE-bench/SWE-bench_Verified` 的 `test` split 中 20 个 instance ID（6 simple、10 medium、4 hard）。该目录只保存 issue 元数据和隐藏 artifact 的哈希，不导入完整仓库或 gold patch；运行时失败、下载失败和 verifier 不可用都必须单独报告。

这 20 条题不构成官方 SWE-bench 运行，也不产生 SWE-bench 官方分数。旧的 5 条小仓库题仍然保留在 Aster 自有 benchmark 中，不能和 `worker-code-v1` 的真实 issue 结果合并。

## Worker 多来源代码集 v2（当前入口）

[`worker-code-v2/`](worker-code-v2/) 替代上面的 v1 作为后续选题入口，共 20 条：Aider Polyglot Python 6 条、BigCodeBench 2 条、Terminal-Bench 5 条、SWE-bench Pro v2 2 条、Verified 5 条。v1 仅保留来源历史。每个来源的固定 revision、下载 URL 与 SHA-256 见 [`sources.json`](worker-code-v2/sources.json)；原始难度、自定义难度、可拆分性和已知 grader 缺口逐题记录。

初始材料与 grader 下载到 `artifacts/private/worker-code-v2/`，不放入 Aster 产品代码或模型的可见工作区。Aider 原始公开测试仍是公开接口契约，独立评分使用不可修改副本；其余隐藏材料只给控制端。来源版权各自保留：Exercism Python MIT、BigCodeBench annotation/Terminal Apache-2.0、SWE/Pro harness MIT、具体源码仓库按原许可（包括 Flipt GPL-3.0、OpenLibrary AGPL-3.0）。参考预检与容器环境缺口见 [`PREPARATION.json`](worker-code-v2/PREPARATION.json)，不能把准备结果当作模型能力或官方成绩。
