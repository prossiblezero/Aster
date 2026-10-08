# SWE-bench Verified：固定十题子集

2026-10-08。用户要求运行一个简单/中等/困难为 3/5/2 的子集。本次只评价当前 Aster，每题一次，不做机制消融或根据成绩调参。

- 题目来自 `SWE-bench/SWE-bench_Verified` 的 500 条 test 数据，原始数据响应的地址与哈希见 `dataset-source.json`，固定题目见 `manifest.json`。
- 上游专家时间 `<15 min fix`、`15 min - 1 hour` 合为 simple，`1-4 hours` 为 medium，`>4 hours` 为 hard。这是人工修复时间分层，不是模型难度测量。
- 从无需编译项目本体的 Django、Sphinx、SymPy、Xarray 中选题；Django 限 Python 3.9 可运行的 5.0 之前版本。排除前次五道 Verified 模型评测题及已用于 CDF 复验的 `sympy__sympy-13878`。随机种子 20261008，先抽 hard，再 medium、simple，每个仓库最多三题。它是受运行环境约束的分层样本，不代表整个榜单。
- 冻结当前生产源码及模型配置。使用 `.env` 中的模型和服务，不输出凭据；64,000 上下文、8,192 输出上限。每题统一最多 60 次模型调用、90 步工具、1,000,000 token、900 秒前台时间。沿用 Aster 原有传输重试，计入每次已报告消耗；不重新采样已经运行的题。
- 使用默认单 Agent 配置，记忆和自动整理开启，每题独立工作区、空白长期记忆，结束后沿用 CLI 的至多 15 秒整理。不开启跨题经验共享，也不测试 Worker 对照。
- Agent 只收到原始 `problem_statement`、对应 `base_commit` 的源码和公开环境说明，不提供参考补丁、测试补丁、隐藏测试名称、历史解法或人工解题提示。执行期间网络关闭。
- 此机没有 Docker。沿用项目已有的 Landlock/seccomp 隔离与原始 F2P/P2P 测试的本地评分适配器；记录 Python、依赖和命令适配。成绩称为 **SWE-bench Verified 十题本地子集 resolved rate**，不称为官方 Docker 榜单分数。
- 模型运行前检查同一评分入口：未修复版本有目标失败，参考补丁全部必需测试通过，必需测试无缺失。若环境仍不能运行，保留题目并报告 environment error，不因模型失败换题。
- Agent 结束后，在独立副本恢复原始测试/运行器，再加入上游 test patch。全部 `FAIL_TO_PASS` 与 `PASS_TO_PASS` 均通过才算 resolved；缺失或跳过不算通过。正常终止、部分测试通过、模型费用分别报告。预算结束后留下的补丁同样接受评分。
- 保存每题补丁、结束状态、用量、测试结果和脱敏 trace；原始数据、参考补丁、评分工作区及完整运行工件在 `artifacts/private/swebench-verified-20261008/`。不修改历史评测或生产实现。
