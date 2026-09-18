# Memory 机制与项目资料检索试点

本文件在问答请求前冻结。遵循根PROTOCOL；这是操作员种入的有来源摘录与只读资料问答，不是自主学习或代码开发收益。

## 种入与离线诊断

从已经通过基础验收的Campus Planner快照复制全部产品文件。公开docs与rules逐字节一致；始终保留读取权限，不删除、遮蔽或修改问答副本中的来源。通过真实Runtime.step的read_file→tool_load(memory_store)→memory_store路径，记录data-contract、time-rules、calendar-rules、backup-rules中各一条“稳定项目约定”原文，tags为空；保存每条read_file的evidence_id、source_sha256和memory_store结果。不得直接构造Memory JSON，不调用模型。该种入不计入自然保存漏斗。

仅在单独诊断副本上，新建Runtime读取持久化Memory：四个预先固定的中文改写查询、一个无关查询，以及另一个副本修改存储约定源文件后检索旧摘录。报告预期条目是否召回、无关结果、失效行为。检索是中文二元词组匹配，不据此声称一般语义理解。所有正式条件使用未经诊断计数更新、未经来源修改的同一seed。

## 真实只读对照

固定顺序：storage-backup off→on，calendar-time on→off；每条件一次，共4个新的Runtime会话，不重复抽样。两组完整产品文件及初始.aster/Memory/来源证据逐字节相同，只切memory_enabled；evolution和Dream关闭，Skills不配置。开启组允许直接使用召回摘录，关闭组可读相同docs，双方均可使用普通工具。每次gpt-5.6-sol / Responses，128000上下文、4096输出上限、8工具步、6模型轮、360秒、单请求180秒；required沙箱、read_only、禁网、Worker关闭、stream关闭、只读并行1。

问题明确要求仅JSON最终答案。storage-backup核对8个事实；calendar-time核对9个事实（包含北京时间2026-10-09 00:10换算为UTC的起止时刻），再分别核验引用是否涵盖对应文档。字段值、类型及来源引用由独立脚本确定，失败/非JSON/额外文字如实记为未全通过，不用另一个模型打分。只读结果需产品字节不变。

从保存的第一个实际请求中检查seed原文与证据引用是否同时出现，统计召回条目；从本轮真实trace统计read_file/search总数和源文档访问次数，保留usage缺失与请求覆盖率，不把未知量当0。报告答案字段通过数、来源正确性、总体通过、时间、模型轮、工具步。Memory开启改变了提示及可用工具，单次成对差异只是整个开关的观察，不能严格归因为某个检索算法。

memory_probe.py prepare拒绝覆盖已有seed/manifest，并冻结本协议、问题、公开规则、评分脚本、run_project、产品摘要及源码提交。它只产生正式启动命令，不自动请求模型。root确认基线后执行run_project；audit只读这些正式结果并写入尚不存在的comparison.json。诊断失败不通过改问题、改标签或遮蔽文件来修饰。

## 事前审查修订：证据引用评分

2026-09-18，在任何Memory正式请求和seed准备之前，代码审查发现原评分器只认识种入时的read_file证据ID，可能误拒模型在本轮重新读取同一文档后引用的新证据。此问题不是观察候选失败后调整答案标准。修订只扩充合法引用映射：从本轮trace接受成功read_file的outcome.evidence_id或该tool_result事件hash，要求来源路径属于冻结规则且source_sha256与种入来源相同；失败工具、非读取工具、未知路径或不同来源摘要均不接纳。题目、正确事实、条件顺序、预算和是否通过的要求不变。增加离线真实Runtime读取与错误引用拒绝测试后重新冻结Memory协议和脚本。
