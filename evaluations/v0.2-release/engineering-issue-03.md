# 真实TUI切换暴露的历史追踪字段兼容问题

首次维护任务在真实TUI完成修改后，`/model continuation` 被 `unsupported_history_fields` 拒绝；按钮输入已送达，原profile及会话保持不变。产品32项独立浏览器检查通过，但公开入口链的切换门槛失败，`maintenance-initial/summary.json` 的 `passed=false` 保留。

根因是网关在 Responses 原生 assistant 输出上增加 `metadata.turn_id` 与 `internal_chat_message_metadata_passthrough.{turn_id,create_time}`。历史规范化对额外字段严格拒绝；空历史的切换烟雾检查没有覆盖真实长历史。

`7adc9e1` 仅允许已观测的这两类追踪容器，验证非空有界 turn_id 和有限非负时间后，在规范化历史中移除；原生历史继续归档。未知字段、错误类型、用户消息或非Responses协议上的这些容器仍拒绝，不通过放宽整个历史校验规避失败。新增回归在旧代码上复现错误，修复后同协议切换、归档与恢复以及反例通过。

补充验收使用原场景剩余3工具步/9模型轮/467秒，通过真实TUI重新载入已完成checkpoint并切换后解释。它属于明确记录的框架修复补验，不能改写首次结果；新请求和耗时另计入累计成本。没有重做产品或改变网关/需求。
