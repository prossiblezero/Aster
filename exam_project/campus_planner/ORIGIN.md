# 来源

本项目由真实 Aster Runtime 编写，实现、自测和产品 README 均没有操作员手工修补。

基础实现来自 `base-recovery-2-stream`，因模型服务错误中断；`delivery-base-finish` 补写 README 并执行正式测试。`backup-docs` 实现备份，最终 `delivery-integrated` 在其上增加日历。两次更早的首轮空响应、后续中断与人工交付反馈都保留，不能称为一次自主成功。

- 最终 17 项 Node 自测通过：`node --test`。
- 最终 26 项独立浏览器检查通过，覆盖基础交互、移动端、JSON备份和ICS日历下载。
- 产品字节与最终 Aster 候选逐文件一致：[交付摘要](../../evaluations/benefit-20260918/delivered-product.json)。
- 完整实测与限制：[报告](../../evaluations/benefit-20260918/REPORT.md)。

启动与使用见 [README.md](README.md)。无需第三方运行依赖；评测驱动、轨迹、模型设置及评分结果均不放入本目录。
