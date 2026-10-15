# 工程测试指南

在 Conda `aster` 或由它派生的验证环境中，从仓库根目录运行下面的命令。先确认 `python -c 'import sys, aster; print(sys.executable); print(aster.__file__)'` 指向预期环境与源码；验证 wheel 时应指向隔离环境的 `site-packages/aster`。

## 选择运行范围

| 场景 | 命令 | 用例数（2026-10-07） |
| --- | --- | ---: |
| 日常快速检查 | `python -m pytest -q -m smoke` | 37 |
| 提交前核心回归 | `python -m pytest -q -m core` | 424 |
| 发布、合并较大重构 | `python -m pytest -q` | 1213 |
| 已跑核心，只补其余回归 | `python -m pytest -q -m "not core"` | 789 |

`smoke ⊂ core ⊂ 全量`。选足以覆盖此次改动的范围，不必把三层依次执行。快速检查覆盖启动、读写及正式验证、权限边界、压缩与恢复、会话分支、流式取消、记忆准入及训练数据契约；它不替代修改模块的完整测试。核心回归覆盖这些主要契约的参数组合与故障路径，完整回归还包含环境生命周期、扩展技能、记忆演化、评测器、更多专项回归和网站。

CI 先执行 `-m core`，再执行 `-m "not core"`，两步不重叠，仍然要求全量通过。新测试即使没有标记，也会进入第二步；无参数的 pytest 始终运行全量。`--strict-markers` 会把拼错的标记报成错误。

这些测试不调用真实模型 API；部分会启动真实的本地子进程、测试运行器与 Linux 沙箱。运行前仍需准备项目依赖、词表缓存、Landlock/seccomp 和可创建命名空间的 bubblewrap，见[沙箱安装](../docs/execution-environment.md#工作区绝对路径与沙箱安装)。快速检查仍需要这些运行条件。

## 按改动模块定位

使用原有测试文件和 pytest 原生筛选即可，不需要记住另一套执行器。下列命令列出常用范围，跨模块改动需同时选择相邻契约或执行核心／全量回归。

| 改动范围 | 命令示例 |
| --- | --- |
| 会话和终端 | `python -m pytest -q tests/test_session*.py tests/test_tui*.py tests/test_cli_contract.py` |
| 模型协议、流式与配置 | `python -m pytest -q tests/test_provider_events.py tests/test_protocol_pairing.py tests/test_streaming.py tests/test_model*.py tests/test_history*.py tests/test_profile_redaction.py` |
| 上下文预算与归档 | `python -m pytest -q tests/test_context*.py tests/test_stable_prefix.py tests/test_observation_archive.py tests/test_usage_budget.py` |
| 工具和执行边界 | `python -m pytest -q tests/test_tool*.py tests/test_safety.py tests/test_sandbox*.py tests/test_workspace*.py tests/test_fresh_read.py tests/test_write_approval_race.py tests/test_dependency_environment.py tests/test_environment*.py tests/test_shell_heredoc.py tests/test_network_system_files.py tests/test_node_verification.py` |
| Memory 与 Skills | `python -m pytest -q tests/test_memory*.py tests/test_skill*.py` |
| Runtime 与训练接口 | `python -m pytest -q tests/test_harness.py tests/test_runtime*.py tests/test_interaction_workers.py tests/test_recovery_contracts.py tests/test_rl_contract.py tests/test_training_contract.py` |
| 评测器 | `python -m pytest -q tests/test_benchmark*.py tests/test_robustness_benchmark.py tests/test_context_comparison.py` |
| 教学网站 | `python learn_web/build.py`，再运行 `python -m pytest -q learn_web/tests` |

定位一个失败时，可运行 `python -m pytest -q tests/test_session_tree.py -k branch`，或复制报错中的完整 `文件::函数[参数]` 编号（用引号包住）。`python -m pytest -q --lf --last-failed-no-failures=none` 只重跑缓存中的失败用例；它不作为提交验收。`--collect-only -q` 查看选择清单，`--durations=10` 找出当前最耗时的测试。

## 新增和维护

- 优先在对应模块的现有文件里补回归，不复制一个只为单次修复存在的测试文件。保留具体行为和触发条件，不以测试数作为成果指标。
- `pytestmark = pytest.mark.core` 表示该文件属于核心回归；其中少量代表性场景使用 `@pytest.mark.smoke`。新增核心模块时，在模块内声明标记即可。
- 快速检查无需重复整个参数矩阵。例如 `pytest.param("quit", marks=pytest.mark.smoke)` 只把这个退出入口加入快速检查，其余别名依然在核心／全量运行。所有 smoke 用例必须同时属于 core。
- 不同协议、权限、失败阶段和副作用的组合应保留。只有证明输入和被验证行为相同，才合并重复参数；不能把多项测试塞进循环来降低报告数字，也不能为提速放宽断言。
- 快速层保持可在本机十秒量级运行的代表性范围；新增明显较慢的场景优先放核心或全量。这个时间是维护目标，不是跨机器的性能承诺。没有默认并发插件，避免共享环境和时序测试产生额外干扰。

最初引入分层标记时，仅增加选择标记，没有新增测试函数、删除断言或缩减参数矩阵。当时整理前后的 1011 个 pytest 用例编号及顺序一致，37 项快速用例都包含在核心中，403 项核心与 608 项其余回归无交集且合并后等于全量。后续新增测试后的数量见上表。数量是参数化展开后的实例数，不是手写函数数，也不是 Agent 真实任务通过率。
