# 来源

本项目由真实 Aster Runtime 编写，实现、自测和产品 README 均没有操作员手工修补。

基础实现来自 `base-recovery-2-stream`，因模型服务错误中断；`delivery-base-finish` 补写 README 并执行正式测试。`backup-docs` 实现备份，最终 `delivery-integrated` 在其上增加日历。两次更早的首轮空响应、后续中断与人工交付反馈都保留，不能称为一次自主成功。

- 最终 17 项 Node 自测通过：`node --test`。
- 最终 26 项独立浏览器检查通过，覆盖基础交互、移动端、JSON备份和ICS日历下载。
- 产品字节与最终 Aster 候选逐文件一致：[交付摘要](../../evaluations/benefit-20260918/delivered-product.json)。
- 完整实测与限制：[报告](../../evaluations/benefit-20260918/REPORT.md)。

启动与使用见 [README.md](README.md)。无需第三方运行依赖；评测驱动、轨迹、模型设置及评分结果均不放入本目录。

## 2026-09-18：撤销最近一次删除维护

本轮改动来自真实公开 `aster --tui` 的 `maintenance-initial` 候选，实现、测试和 README 由 Aster 修改；操作员仅逐文件复制交付并记录来源，没有手工补写产品实现。

- 新增最近一次删除撤销，保留原 ID、内容与完成状态；恢复后正常持久化，刷新清空撤销机会。
- Aster 自测 `node --test`：19 项通过；独立浏览器验收：32/32，通过原26项与新增6项撤销场景。独立浏览器验收不等于 Aster 自主浏览器测试；Aster 的沙箱浏览器尝试未成功。
- 真实 TUI 已在修改后下一轮生成期间取消、退出并恢复同一会话；首次累计47工具步、21模型轮、432.176秒，没有重复执行同一调用ID。
- **首次完整生命周期门槛未通过**：空闲 `/model continuation` 被 `unsupported_history_fields` 拒绝，未完成切换后的解释请求。原失败轨迹保持不变，不能把本产品浏览器通过写成整个发布验收通过。
- 候选来源版本：`610d7a7189efe37ccfff83150bde32bc83fb6cfe`。本次框架之后针对已观测的追踪字段进行修复；后续验证如有发生应单列，不覆盖首次结果。
- 产品字节来源与 SHA256：[交付摘要](../../evaluations/v0.2-release/maintenance-delivery.json)、[文件清单](DELIVERY-MANIFEST.json)。
- [首次产品评分](../../evaluations/v0.2-release/maintenance-initial-product.json)与[首次执行记录](../../evaluations/v0.2-release/runs/maintenance-initial/summary.json)。

### 单独补充：框架修复后的切换验证

修复 `7adc9e1` 仅允许丢弃已确认的 Responses 追踪字段（受限 `turn_id` / `create_time`），继续拒绝其他未知元数据；原生历史保留归档。随后进行了一次公开 TUI 补充验收：`/resume` 后空闲 `/model continuation` 成功，新的模型请求与返回标签均为 `gpt-5.6-sol`，同一会话正确解释撤销机制、19项已有自测及浏览器验证边界，没有调用工具或改产品文件。该标签仅是服务报告的模型身份。

补充使用2工具步、4模型轮；与首次累计49工具步、25模型轮、514.612秒，仍在50/30/900预算内。补充结果保存在[独立记录](../../evaluations/v0.2-release/runs/maintenance-switch-supplement/summary.json)，**首次切换失败没有被覆盖或改为通过**。
