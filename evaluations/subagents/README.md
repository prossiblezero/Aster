# 十题子任务评测

十道固定调查/审查任务、80 个行为问题、七个上游源码快照。旧六题和新增四题的定义与来源分别保存在 `datasets/subagent-light-20260930/`、`datasets/subagent-ten-20261003/`。

这里从历史执行器中提取了实际使用的公共代码：`support.py` 负责工作区、问题和评分，`ledger.py` 负责全队预算与用量，`shared.py` 负责源码快照和哈希。没有依赖旧 Aster Git 提交或维护者解释器。

## 准备与冻结

在仓库根目录、已安装 Aster 的 Python 环境中，先下载固定 URL 的上游材料并校验 SHA-256：

```bash
python evaluations/subagents/fetch.py
```

配置 `.env` 后冻结本次当前源码、数据、条件与顺序：

```bash
python evaluations/subagents/evaluate.py freeze current --repeats 1 --strategy adaptive
```

冻结会运行独立行为 oracle，但不调用模型 API。它要求可用的模型配置，用于记录随后运行的身份与预算。需要仅离线验证时可临时设置 `ASTER_API_KEY=offline`；该占位值不能用于后面的 run，实际运行前必须用真实配置重新冻结一个不同名称的批次。

`adaptive` 允许模型在 team 条件按需委派；`two_children` 固定两个子任务；`parent_child` 由父方和一个子任务分工。不同策略不能合并成同一对照。

## 运行与检查

下面才会调用真实模型，完整十题有 Solo/team 两个条件：

```bash
python evaluations/subagents/evaluate.py run current
python evaluations/subagents/evaluate.py summarize current
python evaluations/subagents/analyze.py current
python evaluations/subagents/compare.py current
```

源码、缓存和运行证据进入 `artifacts/private/`；聚合诊断写到本目录下的批次子目录，均被 Git 忽略。批次同名恢复前会校验源码、数据、配置及结果身份，不应手改旧结果继续运行。

工程评分回归：`python -m pytest -q tests/test_subagent_evaluation.py`。实际下载后再执行冻结，可以验证源码快照、题目和 oracle 的组合，而不是只验证导入。

这是开发集上的调查与引用评分，不是通用代码修复成绩。父任务通过、子任务交付、事实正确、引用正确及完整用量分别统计；未知用量和服务失败保留。设计背景见[优化历程](../../docs/optimization-history.md)。
