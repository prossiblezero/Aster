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

参考官方仓库 <https://github.com/SWE-bench/SWE-bench> 的任务结构和测试思路，读取时版本 `02e7a74ffd0b707aab73d203fe87bdc7c76afc8e`。本项目**未导入或运行官方 SWE-bench 实例**，没有 SWE-bench 分数。5 条小仓库题也不等同于 SWE-bench 真实 issue 的难度。
