# 只读工具并行对照协议

两组真实任务已在冻结 `59ca274f69665100dc20e3ef7343d009a84e3c35` 各完成一次，独立验收均为 7/7，无人工反馈或候选修复；见[报告](report.md)。并行组两批只读执行真实重叠、峰值 3，但六个相同小文件的批次墙钟为 12.58 毫秒，串行为 8.84 毫秒；本次没有测得只读批次提速，端到端差异不能归因于并行。

两组由 Aster 从仅含相同 `fixtures/` 的目录完成 [tagmerge 需求](requirements.md)，CLI、README、自测均由 Aster 编写。提示要求理解六个输入并实现通用合并，不规定工具调用数量、批次组成或并行策略。操作员只负责需求、数据、驱动和独立验收，不给候选答案、不修改候选。

固定 `gpt-5.6-sol` / Responses / 同一网关、128,000 总上下文、32,768 输出、300 秒请求超时、30 工具步、16 模型轮、1,800 秒上限；`stream=False`，Memory/Dream/Worker 关闭，required 沙箱、禁网。唯一配置差异为 `read_parallelism=1`（serial）或 `3`（parallel）。两组各一次串行执行，不因模型没有产生合格批次而追加尝试。

复用已验证的 stream_comparison 驱动中的源码和导入路径核验、HTTP 只读计时。操作员预先设置 `PATH` 前置 aster 环境，以及 `PYTHONPATH=<冻结根>/src:<冻结根>/scripts`。先用指定 Python 运行 `run.py --source-path <冻结根> --describe-source` 保存全部 Python 文件清单；正式命令为：

```text
/home/dzh/.conda/envs/aster/bin/python exam_project/parallel_comparison/run.py \
  --source-path <冻结根> --source-commit <提交SHA> --source-digest <聚合SHA256> \
  --condition serial --env-file /home/dzh/Aster/.env
```

第二组仅改 `--condition parallel`。驱动拒绝覆盖已有组，比较两组源码、端点指纹、除并行度之外的全部配置、需求/验收器/两个驱动及六个 fixture 的哈希；前后核对源码与实验身份。评分器先验收只有 fixture、没有实现的初始项目，确认全部 7 项执行且整体失败，随后在隔离副本中独立验收候选，评分异常仍保存 Runtime 与 usage。

产品 `tool_batch_finished.duration_ms` 记录整批工具墙钟。驱动只在既有 `ToolRegistry.execute` 外包一层首尾时间戳，记录实际开始/结束、线程、call_id、状态，不加 sleep/barrier、不改变返回值或执行顺序。区间重叠表示工具执行在时间上重叠，不等于 CPU 同时执行或有对应倍数加速；微小计时开销两组相同。

从有效 `model_reply` 识别机会：至少两个调用且全部属于 `read_file/search/list_files`。逐批记录可并行机会、真实执行数和峰值区间重叠；被权限/参数检查拒绝的调用不会被记作执行。混有写、Shell、测试的批次不算机会。权限 deny、blocked 工具另列，工具批次时间、端到端、独立验收和终态用量分开报告。

如果 parallel 组没有合格批次或没有观测到执行区间重叠，如实记录“未测到”，不从配置上限、工具名称或既有 13 项屏障回归推断真实收益。即使有重叠，单任务/每组一次及不同自主轨迹也不能支持普遍加速结论；不把缓存或 Memory/Skills 的效果混入归因。

离线检查不调用模型、不编写候选：

```text
PATH=/home/dzh/.conda/envs/aster/bin:$PATH /home/dzh/.conda/envs/aster/bin/python -m pytest -q exam_project/parallel_comparison/test_driver.py
```
