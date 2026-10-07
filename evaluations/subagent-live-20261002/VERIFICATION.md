# 真实模型复测验证

## 输入与完整性

- 第一个任务回合前冻结协议、驱动、评分器、账本依赖、任务数据、两版源码及非敏感配置，见 FREEZE.json。
- 两版源码分别与 subagent-incremental-20261002 的 baseline/candidate 55 个生产文件哈希完全一致。基线包含此前尚未提交的实现，不取 Git HEAD。
- 执行子进程先断言 `aster.__file__` 指向指定冻结版本，再运行原有 Runtime 和工具链；每回合前后验证源文件。
- 开始前通过 **48 个可执行行为事实、24 个评分负例**检查。该检查未覆盖合法嵌套函数引用，后来发现的缺口明确单列，没有声称评分器因此已完备。
- 结束后确认 **24 个预定 task/arm 唯一组合**全部存在，顺序与协议一致，没有未结束回合、覆盖或择优重跑。
- 全部 24 个父运行完成、全部 31 个子运行完成；31 包含一个子会话的自动续跑。源码及任务文件均未改变，运行 usage 与请求账本完全一致，无 runner error。
- 当前 checkout 的全部生产文件仍与冻结 candidate 一致。本轮只新增评测和诊断脚本、记录与文档。

## 服务与开销

最小健康请求成功后才启动正式批次。使用项目 `.env` 的现有配置，没有输出凭据或改写 `.env`；环境与依赖版本见 ENVIRONMENT.json。

任务请求 209 次，HTTP 尝试 210 次，成功 209 次，传输失败 1 次。传输超时约 120 秒，原有有界重试成功；对应基线 Django 审查回合。失败尝试缺失 total usage，未按零成本处理。正式任务已报告 token 为 2,480,184，健康请求另为 4,395。模型响应报告名称一致，不能据此进一步验证供应商内部路由。

当前双子任务的六个首次状态调用均观测到一个交付与一个仍运行任务。父方模型调用从基线 7 次增加到 23 次；事件记录能确认提前返回和额外协调，不能单凭提前响应数断言父方完成了有效独立工作。

## 评分核对

主分数仍由冻结的 subagent-light-20260930 评分器生成，没有修改最终答案。事后 diagnose 区分行为错误、引用拒绝、格式错误、HTTP 重试及费用缺失；audit_children 比较已有子方/父方答案，不发起新模型调用。

`audit_citations.py` 仅用于公开的附加敏感性分析。运行途中发现旧索引漏掉函数体中的嵌套定义；全部回合结束后对 24 个输出统一重查。它断言所有旧 AST 范围保持原值，并保留路径、行号、行为、ID 与完成条件。四条合法嵌套函数引用被旧评分器拒绝，分别影响当前双子任务和单子任务的同一道 OpenLibrary 审查题。真正的越界行号和拼接符号没有放行，主 results.json / summary.json 未被改写为附加分数。

这修正了对失败原因的解释，不是事前冻结的主评分，也没有修补模型输出。新的评分器版本和正负例覆盖应留待独立下一批次。

## 检查与复算

使用 `/home/dzh/.conda/envs/aster/bin/python`，Python 3.12.14。评测/诊断脚本 Ruff 通过，差异空白检查通过。生产实现沿用上轮已经完成的 1,105 passed、4 skipped 与 wheel 170 passed；本轮没有重跑或冒称新增一次完整生产回归。

README、能力边界和教学文档已同步，教学站点重新生成，页面检查 **6 passed**，1.21 秒。实际请求启动顺序与冻结的 24 个位置逐项一致；报告本地链接全部可解析。分析脚本和输出哈希见 ANALYSIS-HASHES.json，附加审计没有覆盖主结果文件。

```bash
python evaluations/subagent-live-20261002/evaluate.py summarize
python evaluations/subagent-live-20261002/diagnose.py
python evaluations/subagent-live-20261002/audit_children.py
python evaluations/subagent-live-20261002/audit_citations.py
python -m ruff check evaluations/subagent-live-20261002
```

上述复算不调用模型。原始轨迹、账本、工作区和冻结源码位于忽略目录 artifacts/private/subagent-live-20261002。原来两批 401 记录和历史成绩完整保留。
