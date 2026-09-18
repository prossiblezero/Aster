# 维护场景结果与暴露的问题

2026-09-18。候选与评分协议在启动前冻结，原公开TUI执行结果位于 `runs/maintenance-initial/`，独立产品评分位于 `maintenance-initial-product.json`。交付至 `exam_project/campus_planner` 的实现/测试/README均是Aster生成的候选原字节，操作员仅复制和添加来源记录。

## 首次结果

- 32/32独立浏览器场景通过，其中原26项、新增撤销6项。Aster自己的Node测试19/19通过。
- 已实现真实键盘取消：首个修改完成后下一轮生成期间Ctrl+C，确认provider取消事件；Ctrl+Q退出，重启后 `/resume`，原session ID一致，没有重复执行同一调用ID。
- 47工具步、21模型轮、432.176秒；未使用人工服务故障恢复或产品返修。
- **完整生命周期首次未通过**：空闲 `/model continuation` 显示 `Model selection failed: unsupported_history_fields`；没有模型切换事件，解释请求未发送。这是框架历史转换拒绝，不是驱动按键失败或产品撤销缺陷。首次summary保持 `passed=false`。

## 定位、修复与单独补充

真实Responses历史的reasoning、message、function_call携带 `metadata={turn_id}` 和 `internal_chat_message_metadata_passthrough={turn_id, 可选create_time}`。原严格字段检查拒绝所有此类追踪元数据。同协议空闲切换的最小回归先复现相同错误，再作受限修复：只允许这两个容器中的已观测标识/时间字段，检查类型和范围，转换时丢弃，原native历史另行归档。未知字段、语义变化、坏类型仍拒绝。77项history/session专项通过；原100条真实历史在纯内存转换为81条，19条不跨模型携带的reasoning被计数，原checkpoint字节没有修改。

框架修复提交 `7adc9e1` 后只做一次剩余预算内的补充：公开TUI `/resume` 自动继续先花2工具步、3模型轮；空闲 `/model continuation` 成功，另1轮生成正确解释了原会话中的撤销机制、19项测试和浏览器验证边界，没有执行工具。两个新run仍属于原session；切换请求/返回标签均为 `gpt-5.6-sol`，标签只能证明服务所报告身份。

补充使用2步、4轮，累计49步、25轮、514.612秒，在50/30/900预算内。产品文件未变，未重跑候选生产，未修改首次结果。补充证据位于 `runs/maintenance-switch-supplement/`；`events.json`只保留操作结构和计数，`event-export.json`记录源trace摘要，原trace/checkpoint仍在候选 `.aster`。

## 工程观察

- 模型先用 `node --check app.js` 检查浏览器ES模块，得到常见CommonJS解析错误，随后自行改为 `node --input-type=module --check < app.js` 成功。执行环境说明虽已提供，模型仍需纠正；不是产品JS语法缺陷。
- 单次Shell内启动静态服务器并自行 `kill` 出现 `Operation not permitted`，导致等待/超时；Firefox headless尝试也超时。现有沙箱进程权限与可选浏览器验证仍有边界，不能宣称Aster已自主完成浏览器验收。Aster最终说明中如实报告未完成浏览器点击自动化和截图。
- `ps -ef`在沙箱中报需要挂载 `/proc`，整条包含 `|| true` 的命令退出成功。trace保留stderr与退出码，没有把它当成产品验证证据；这是执行环境限制，应保持可发现。
- 候选副本不是Git仓库，模型运行 `git diff --check`失败。应使用已有基线工具或先识别版本库状态；无需为评分复制父仓库 `.git`。
- `/resume`已完成任务会立即继续生成，并可能受旧completion feedback影响再次运行测试；补充实际花了2工具步和3轮才进入空闲切换。预算已计入，这提示未来可考虑公开“仅载入、不立即继续”的恢复入口，但本轮没有新增接口。

## usage与缓存

首次21轮包含1次主动取消且usage未知，其余20次已知：input 700,392、output 8,603、cache-read 103,424，已知输入的缓存占比14.77%。补充4次全部已知：input 199,952、output 724、cache-read 100,352，占比50.19%。这不是controlled缓存收益比较；补充天然携带很长的既有会话，并且切换了profile。取消请求的真实消耗不能按0补齐。记录的provider diagnostics未标出模型标签变化或计数矛盾，不据此推断实际底层权重或训练数据可信度。

交付摘要 `maintenance-delivery.json`分别记录产品通过、首次生命周期失败和修复后单独补充通过。不能把后者回写为“首次全链路成功”。
