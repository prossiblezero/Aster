# 本轮实际借鉴的设计

此次先读当前实现和失败轨迹，再对照公开源码。只借用能解决已复现问题的机制，没有复制大型系统的服务层或引入新的框架依赖。

| 来源 | 实际阅读的位置 | Aster 采用的机制 |
|---|---|---|
| OpenAI Codex | [history.rs，固定版本](https://github.com/openai/codex/blob/99cda7a9a5997c36aff413d42a222bdb5f95c434/codex-rs/core/src/context_manager/history.rs) | 删除历史时维护工具调用与结果关系。Aster 的 `history_groups` 以完整交换为裁剪单位，推理元数据随组保留。 |
| Pi | [compaction.ts，固定版本](https://github.com/badlogic/pi-mono/blob/ceea48f5d5d12fd7915dfefba2835ccd55f23bb9/packages/coding-agent/src/core/compaction/compaction.ts) | 有效截断点不能落在工具结果前。Aster 进一步用最终请求结构和工具定义计量预算。 |
| Pi | [agent.ts](https://github.com/badlogic/pi-mono/blob/main/packages/agent/src/agent.ts) | 外部指导先排队，在轮次边界消费；发出取消与等待清理是两件事。Aster 使用已有 asyncio Task 和短队列实现。 |

记忆部分对照了本地 nexa 的反馈思路、pico-v3 的记忆检查以及 Codex 的记忆提取提示设计，最终采用保守的来源摘录、文件新鲜度检查和确定性反馈排序。它不是对这些项目完整记忆算法的复刻，也没有引入模型来裁决记忆真假。

Claude 官方文档在本轮调研时返回 403，因此本轮没有把未读取的最新内容作为实现依据。此前的需求讨论仍可保留，但验收结论来自当前 Aster 的代码和评测。没有使用泄露源码。

## 为什么保持这些简化

- 上下文策略固定：旧观察让位于当前请求、完整工具交换和最近证据；无法满足预算就明确停止。不训练或自演化裁剪策略。
- Hooks 只有前置、成功、失败、停止四个时机；前置异常拒绝动作，后置异常记录反馈。权限仍由统一入口负责。
- 记忆只在来源可核验时入库。使用反馈会影响排序和失效状态，Dream 不凭空生成新事实。
- 工具分发只按“工作区操作”和“运行状态操作”划分。删除重复及不可达实现；没有新增通用注册框架。
- rollout 环境复用 Runtime，复制初始仓库保证 episode 隔离。只有可信的独立验证器可以给成功奖励。

这些边界对应 [修复计划](repair-plan.md) 和 [专项评测](../artifacts/benchmark-v3-mechanisms/report.md)。后训练方法及训练数据组织等后续讨论再定。
