# 会话改造后的真实编码回归（运行前冻结）

目标：补齐会话操作评测未覆盖的真实模型任务通过率与资源消耗。接受标准是本批同题对照中未观察到完成率下降；不要求会话管理提高模型能力或降低费用，也不以小样本声称统计非劣。

## 固定条件

- 基线：`a3f338b43bfea61cc7bae563c07168f502cc34b5`，会话树改造前。
- 候选：`edec1aa1a7ca84550bcf95df42c142489ee665fa`，包含会话树、鼠标入口和测试分层。
- 两版复用各自提交中的 `scripts/benchmark.py`、`benchmark_support.py` 和 `compare_benchmarks.py`；这些文件及整个 `datasets/` 在两版间完全相同，不修改评分器。
- 固定现有 repository 组全部 5 题：`multi-file-refactor`、`debug-and-fix`、`add-endpoint-with-test`、`config-migration`、`dependency-upgrade`。每题各用 core/all 工具集运行一次，每版 10 次，共 20 次真实尝试。两种工具集分别统计，不作为两次重试合算 Pass@2。
- 每次独立干净工作区，32 个工具步骤、24 轮；模型不接触参考动作或独立评分器。最终由原有隔离评分副本运行隐藏测试，并检查 Runtime 完成条件。
- 使用 Conda `aster` 的 Python，两版均显式设置 `PYTHONPATH=<对应 worktree>/src` 并断言实际导入路径。不得更改共享环境的 editable 安装来切换版本。
- 使用同一 `/home/dzh/Aster/.env`：`gpt-5.6-luna`、Responses、非流式；有效上下文预算 908,668、输出预算 32,768、单请求超时 90 秒，网关默认采样参数。身份文件保留完整非敏感配置。沙箱 required、审批 auto、工具网络关闭、长期记忆关闭。
- 两版进程同时启动，各自并发 1，总任务并发最多 2；各版任务顺序相同。耗时包含真实模型与工具执行，不包含事后独立评分；不能作为纯会话导航性能。
- 先运行两版离线 repository 参考回放，确认原始 fixture 目标失败、回归行为通过、参考修复通过。离线回放不计真实模型通过率。
- 不按结果挑题，不单独重试失败任务。产品原有 HTTP 重试与恢复策略保留，其可观测消耗计入记录。服务端不可见消耗未知。

## 指标与边界

主指标是每种工具集的任务完成率，以及逐题从通过变失败的数量。另列独立代码测试通过率，避免将代码正确但未满足完成合同的尝试混为一谈。

记录全部尝试（含失败）的工具调用数、模型调用数、输入/输出/缓存/总 token、逐项 usage 覆盖及运行耗时；同时给出两版共同成功任务的消耗。没有网关价格与账单时，实际货币费用标记为未测，不套用猜测的官方价格，不把缺失 usage 当成零。

这是 5 道已用于开发的仓库小题回归，不是未见任务、SWE-bench 全集或生产质量估计。任务从新会话开始，**没有在真实模型执行中导航分支或跨会话恢复**；它检验改造后一般执行链有无可观测退步。分支功能与操作便利性证据仍分别来自已有会话工程回归和 [操作评测](../session-ux-20260920/REPORT.md)，不将旧命令工作流数字改称鼠标人效。

## 复现

分别建立上述提交的干净 detached worktree，并使用新的输出目录。以下命令在各自 worktree 执行，`<输出目录>` 每版、每种模式独立，不能覆盖历史证据：

```bash
PYTHONPATH="$PWD/src" /home/dzh/.conda/envs/aster/bin/python -c \
  'import aster; from pathlib import Path; assert Path(aster.__file__).resolve() == Path("src/aster/__init__.py").resolve()'
PYTHONPATH="$PWD/src" /home/dzh/.conda/envs/aster/bin/python scripts/benchmark.py \
  --suite repository --repeats 1 --concurrency 1 --env-file /dev/null --output <离线输出目录>
PYTHONPATH="$PWD/src" /home/dzh/.conda/envs/aster/bin/python scripts/benchmark.py \
  --real --suite repository --repeats 1 --concurrency 1 \
  --env-file /home/dzh/Aster/.env --output <真实输出目录>
```

保留原始报告、每次候选差异、独立评分结果及 trace。公开聚合 JSON、比较结果、配置身份和证据摘要；原始运行存放独立本地目录，不在 `exam_project/` 放评测代码。
