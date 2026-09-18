# 课业清单维护：撤销删除与公开TUI恢复

此场景在 `exam_project/campus_planner` 的独立完整副本中实施，原项目不修改。初始需求为同目录 `maintenance-prompt.md` 的完整内容，启动前记录该文件、本协议、`maintenance-grader.py`、复用的 `evaluations/benefit-20260918/check_browser.py`、Aster源码版本及候选初始文件摘要。操作员不代写候选实现。新增功能由Aster编写、测试和说明。

## 功能验收契约

- 保留原26项浏览器验收，包括备份、日历与无外部联网。
- 提供公开按钮“撤销删除”，确认删除后可恢复最近一条任务的原id、标题、课程、截止时间、备注、完成状态，其他任务保持原样。
- 连续删除甲、乙后只能撤销乙；撤销成功消费此机会，不能再次复制乙或继续恢复甲。
- 刷新不保留删除撤销记录；刷新前已经恢复的任务正常持久化。
- 390px手机宽度能完成删除和撤销。按钮放置方式、颜色和内部实现不限；没有可撤销项时可隐藏、禁用或点击后明确无操作。

独立评分器复用原 `BrowserAudit`、只读静态服务器及 `check_base/check_backup/check_calendar`；新增6项真实浏览器操作。原公开存储契约是 `campus-planner-v1` 中的 `{version:1,tasks:[...]}`，检查该公共持久化结构及可见界面，不导入候选函数。每项新建浏览器上下文，不沿用宿主登录资料；外部HTTP及WebSocket请求阻断并计为失败。截图、下载和结果保存在评估目录内、候选之外的新路径。

## 生命周期验收与预算

总预算为50工具步、30模型轮、900秒；首次实现、主动取消、退出重开、恢复、模型切换解释与至多一次同服务故障恢复全部累计，不能通过续聊重置外部总账。明显产品缺陷最多一次针对性返修，也消费剩余预算。首次结果和每次后续尝试分别保存。

公开入口为 aster 环境中的 `aster --tui --cwd CANDIDATE --env-file /home/dzh/Aster/.env --config /home/dzh/Aster/.aster/v02-release.toml --profile main --max-steps REMAINING --max-turns REMAINING`。使用真实PTY键盘，不调用Runtime内部方法替代用户操作。

1. 发送冻结需求，观察首个 `tool_result` 中的非空 `changed_paths`。在之后下一个 `model_stream` 的 `begin` 事件出现时发送Ctrl+C。这次取消必须发生在已有修改、下一轮模型生成期间；若模型在同一轮直接完成、未出现触发点，明确记录生命周期场景未完成。
2. 等待取消和checkpoint保存，记录run ID、已修改文件、验证状态、剩余额度；Ctrl+Q退出，确认进程结束。
3. 以剩余工具步/模型轮启动同配置的新TUI，发送 `/resume RUN_ID`。恢复仅使用保存状态，不补写代码。检查原已成功动作的调用ID/工具执行证据，不能重复执行这些动作。
4. 实现完成后在空闲TUI发送 `/model continuation`，再提问：“请基于刚才的会话，用中文简要说明撤销删除如何工作、哪些自测已经执行、有什么验证边界；不要修改文件或重复执行工具。”对照 `model_changed`、新请求的模型标签、会话ID/上下文证据验证切换与历史保留。切换明确使用预先配置的另一个profile，不能作为服务故障时的隐式补抽。
5. 独立浏览器验收全部32项通过，另审查Aster自测、README、生命周期证据及累计预算。浏览器通过不能替代恢复/切换门槛；独立浏览器验收不冒充Aster自主浏览器自测。

模型服务错误最多一次人工恢复，必须记录原失败且剩余额度足够。出现未知Shell副作用、权限拒绝或不兼容会话时不得绕过产品保护。缺少TUI关键事件、异常退出、期限耗尽分别记录，不伪装成完成。

## 离线评分器校验及运行

评分器自己的正负fixtures验证完整字段/稳定ID/完成状态/无重复等独立断言。启动前可对原未实现撤销功能的项目运行一次负对照：期望原26项通过、新增6项失败；它不计为候选成绩。评分器错误需要保留原结果并版本化修正，不覆盖失败。

```bash
/home/dzh/.conda/envs/aster/bin/python -m pytest -q evaluations/v0.2-release/test_maintenance_grader.py
/home/dzh/.conda/envs/aster/bin/python evaluations/v0.2-release/maintenance-grader.py \
  /absolute/path/to/candidate --output /home/dzh/Aster/evaluations/v0.2-release/maintenance-initial.json
```

所有Python命令必须来自aster环境。评分器不发模型请求，不新增浏览器依赖。原始TUI屏幕录制、操作时间/按键、模型尝试和工具结果保留为独立执行证据；凭证与连接地址不写入评估报告。
