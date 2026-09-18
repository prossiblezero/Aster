# 来源

本项目由真实 Aster Runtime 编写，操作员没有修改实现、自测或产品 README。

基础实现来自 `evaluations/benefit-20260918/workspaces/base-recovery-2-stream`，因模型服务错误中断；`delivery-base-finish` 任务补写 README 并执行正式 Node 测试。两次更早的首轮空响应与三次人工恢复/交付反馈均保留，不能称为一次自主成功。

- 6 项 Node 自测通过。
- 12 项独立浏览器检查通过，包含增删改、筛选、存储错误和移动端。
- 版本与文件摘要：[baseline-product.json](../../evaluations/benefit-20260918/baseline-product.json)。
- 完整实测与限制：[报告](../../evaluations/benefit-20260918/REPORT.md)。

启动与使用见本目录 [README.md](README.md)。本产品无需第三方运行依赖；评测驱动、轨迹、模型设置与结果均不放入本目录。
