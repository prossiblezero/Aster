# 12. 从一道 bug 修复题读懂评测

这章按“问题 → 代码 → 运行 → 证据”的顺序阅读，不要求先懂整个 Harness。

## 第一步：为什么不能只听模型说完成

打开 [debug-and-fix 的初始仓库](../../datasets/fixtures/long_session_debug_fix/)。任务要求修复行为，模型的最后一句话不能证明函数变正确。因此我们提前写好 [独立验证器](../../datasets/checks/debug-and-fix.py)：`test_fix` 检查新行为，`test_regression` 检查必须保留的旧行为。验证器在 Agent 结束后才加入副本。

在 [任务清单](../../datasets/benchmark_tasks.json) 中搜索 `debug-and-fix`。`prompt` 是要求，`fixture` 是初始目录，`checks.verifier` 是评分代码，`actions` 是用于检查 Harness 的参考调用。真实模式只传 prompt 和 fixture，不读取参考动作来替模型回答。

## 第二步：让评分标准先经得起检查

```bash
conda activate aster
python scripts/benchmark.py --task debug-and-fix
```

阅读 [benchmark.py](../../scripts/benchmark.py) 的 `baseline_check`：先运行原始版本。目标测试应该失败，原有行为应该通过。再看 [benchmark_support.py](../../scripts/benchmark_support.py) 的 `run_task`：新建工作区，启动同一套 Runtime，执行参考动作，最后交给 `external_check`。

若 reference 失败，先看测试输出：`assert` 失败是行为不符，`ModuleNotFoundError` 是依赖或导入环境错误，`no tests ran` 是验证器无效。不能把这几类失败都叫成“模型不会做”。

## 第三步：把参考动作换成模型

```bash
python scripts/benchmark.py --real --task debug-and-fix --repeats 1
```

`ScriptedPolicy` 按预先写好的动作走，用于检查运行边界。`--real` 换成配置好的模型，由它自己决定读哪些文件、怎样改、何时测试。两者走相同 Runtime；前者通过不意味着后者通过。

这题分别跑 core 和 all 两种工具集合。报告中的 `steps` 是工具调用次数，`turns` 是模型请求次数，`passed` 是所有评分条件都成立，`stop_reason` 是为何结束。某次可能外部测试已通过，却因 Runtime 停滞判断提前结束；这种差异需要读 trace，不能只留成功那一列。

## 第四步：用记忆题理解对照

```bash
python scripts/benchmark.py --task memory_deploy
python scripts/benchmark.py --real --task memory_deploy
```

看 `run_memory`：两组都先读文件并存储证据，再创建新的 Runtime 回答同一个问题。关闭组能重新读原文件；开启组可以召回。`fresh_followup` 检查后续运行没有继承原始聊天和会话摘要，`acquisition_steps` 单独记录学习成本。

这只回答“已有事实能否帮助后续少读文件”，没有证明系统会自行发现优质经验，更没有证明自进化优于固定记忆。不同问题必须另外设计数据集。

## 第五步：主动找反例

读 [test_benchmark.py](../../tests/test_benchmark.py)：故意清空公开测试、注入 pytest 配置、关闭已有记忆、给策略一个不同于题库答案的观察。评测器应当识别错误或使用真实观察。

再运行全量 benchmark。出现红色结果就查看 `checks`、trace 和独立测试输出。v2 曾发现完整输入超预算、持久记忆接纳不合适内容；v3 修复后重跑相同任务，同时保留旧失败记录。评测的作用是找到并解释变化。

新增任务按 [数据集字段说明](../../datasets/README.md) 操作。任务内容放 datasets，脚本只负责执行和判分；具体实验结果在 [评测说明](../benchmark.md) 中查看。

## 第六步：把模块效果与任务能力分开

```bash
python scripts/benchmark_mechanisms.py
```

打开 [记忆场景](../../datasets/memory_evolution_cases.json) 和 [运行场景](../../datasets/runtime_cases.json)。`variants` 指机制开启/关闭两个条件，`expected` 指每个条件本来应该出现什么行为，`goal` 指两组共同想达到的目标。比如关闭恢复时按约定停止，属于契约通过，但没有完成后续修复，所以目标未达成。

[机制报告](../../artifacts/benchmark-v4-mechanisms/report.md) 的两列正是这两个含义。反馈场景把成功/失败标签作为固定输入，检验这些反馈是否改变记忆选择。它证明代码机制生效，不能替代真实模型上的端到端收益实验。

记忆专项包含 12 个准入、4 个来源变化、6 个反馈调整、2 个回滚场景，各运行开启/关闭两种条件。6 个反馈场景包含四个应改善召回的案例和两个不应误删好记忆的控制案例，共同目标由关闭组 2/6 提升至开启组 6/6。可以单独运行 `python -m pytest -q tests/test_memory_evolution.py`。真实任务的“开启记忆更省工具调用”属于召回收益，不能代替“开启演化比固定记忆更好”的实验。

工具分组也需要反例。运行 `python -m pytest -q tests/test_tool_contracts.py -k disabled_capabilities`，检查 [16 组配置](../../datasets/tool_visibility_cases.json) 下的 core/all 定义、加载目录和直接调用。例如禁用 Worker 后，启动、查询、指导和停止工具都应消失；仅关闭记忆演化时，保存和召回工具应保留。工具的完整参数定义是否真的在加载后才出现，由 `test_core_catalog_loads_optional_schema_then_executes_tool` 通过连续 Runtime 轮次检查。

再读 [运行可靠性](13-runtime-reliability.md) 与 [记忆和 rollout](14-memory-and-rollouts.md)，逐个运行配套回归。后训练会复用环境接口，但需要另行设计训练任务、奖励与留出评测，不能直接拿这里的答案训练再报同一份成绩。
