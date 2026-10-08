# 跨会话记忆与成本评测

`cases.py` 定义十个固定项目任务、学习阶段、后续复用请求和独立断言。`run.py` 使用真实 Runtime，统计前台、学习和最多 15 秒后台整理的用量；`summarize.py` 汇总完整两组结果；`audit_requests.py` 可检查另外保留的请求归档。

从仓库根目录、已安装 Aster 的环境运行，先配置 `.env`。以下命令会调用真实模型：

```bash
python evaluations/memory/run.py --arm cost \
  --work artifacts/memory/workspaces --output artifacts/memory/results
```

可用 `--case <case-id>` 限定题目；实际 ID 请查看 `cases.py`。`baseline` / `cost` 只是记录标签，**不会自动切换代码**。比较两个版本时，使用各自隔离安装的解释器分别运行同一套脚本与固定任务、分别指定 `--arm baseline` 和 `--arm cost`，并保持模型、预算、数据与其他条件一致。执行器记录实际导入源码的哈希，拒绝把不同源码续写到同一组结果。

两组十题都完成后：

```bash
python evaluations/memory/summarize.py --output artifacts/memory
```

不要将未知 usage 当成零，也不要把缓存价格敏感性分析当成实际账单。单题运行用于诊断，不能生成完整十题比较结论。原始实验失败与决策已归纳到[优化历程](../../docs/optimization-history.md)，旧的源码压缩包、成绩和轨迹不随项目分发。
