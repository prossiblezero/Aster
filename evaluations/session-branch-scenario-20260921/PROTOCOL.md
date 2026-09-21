# 回到历史位置重新探索：专项评测协议

这轮只回答一个用户场景：完成前 `i` 轮设计后，经历后 `j` 轮探索，用户想从第 `i` 轮继续提出新想法。评测操作便利性、历史隔离、约束保留、交接成本和后续回答质量；不改生产代码。

## 固定场景

前 `i=4` 轮建立三个约束：`storage=sqlite`、`privacy=offline_only`、`timezone=Asia/Shanghai`。后 `j=3` 轮探索云端 PostgreSQL、上传和 UTC 方案，并明确它们只是探索资料。最终任务要求在保留三个约束的前提下设计新的导出方案。

每条路径使用独立干净工作区；前后历史由离线替身预置，避免把共同准备成本混入比较。真实批次只让模型处理“回到位置后的最终任务”，旧总结路径另外让模型生成一次交接摘要。

## 对照路径

- `legacy_summary`：旧版继续请求模型总结，再新建会话、手动交接摘要、执行最终任务。
- `legacy_checkpoint`：旧版通过已保存的运行 checkpoint 恢复第 `i` 轮，再执行最终任务。这条路径用于诚实记录旧版已有的低成本技术替代，不把旧版描述成完全不能恢复。
- `branch_clean`：候选选择第 `i` 轮开分支，不带后 `j` 轮摘录。
- `branch_excerpt`：候选选择第 `i` 轮开分支，并显式带入后 `j` 轮确定性摘录。

旧版没有类型化会话树，候选没有把旧版 checkpoint 路径冒充新功能。操作数只计算从“决定回到第 i 轮”到“提交最终任务”的准备操作；最终任务提交单列。操作定义、人工交接字符数和模型请求数全部保留。

## 指标

- 准备操作数、手动交接字符数、准备阶段模型调用数和 token。
- 分支历史中原始约束是否保留、后 `j` 轮标记是否按选择隔离、摘录是否可用。
- 最终任务的独立约束检查和真实模型回答通过率。
- 最终任务输入/输出/总 token、HTTP 重试及未报告 usage；没有网关价格时不换算金额。

离线批次验证持久化和上下文边界；真实批次验证有限的最终回答质量。样本不是通用 Agent 榜单，也不证明摘要在所有任务上正确。当前实现的摘录是确定性的，不调用模型，最多单条 2,000、总计 12,000 字符。

## 复现

在两个干净 worktree 中分别执行，显式设置 `PYTHONPATH`，使用 Conda `aster`：

```bash
PYTHONPATH="$PWD/src" /home/dzh/.conda/envs/aster/bin/python \
  evaluations/session-branch-scenario-20260921/run.py \
  --variant baseline --mode scripted --output /tmp/aster-branch-baseline-scripted
PYTHONPATH="$PWD/src" /home/dzh/.conda/envs/aster/bin/python \
  evaluations/session-branch-scenario-20260921/run.py \
  --variant candidate --mode scripted --output /tmp/aster-branch-candidate-scripted
```

真实模型把 `--mode scripted` 换成 `--mode real`，并显式传入同一个 `.env`。每次使用新的输出目录；原始运行 trace 保留在输出目录之外的本地证据中，公开报告只复制脱敏聚合结果。
