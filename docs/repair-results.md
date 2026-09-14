# 修复验收记录

本轮以 `4c3b245` 的 13 项问题为清单，先修共享入口，再用独立审查找相邻路径的问题。实现提交包括 `d405773`、`1377204`、`62fedb1`；评测与文档随后单独提交。

## 对照清单

| 原问题 | 本轮落地 | 验证位置 |
|---|---|---|
| 1. 连续成功 patch 被判成循环 | 实际文件变更打断停滞窗口；重复/振荡比较调用和完整结果；恢复窗口有界且可保存 | `test_runtime_repairs.py`、`runtime_cases.json` 恢复及控制场景；相同仓库题真实对照 |
| 2. 最终输入超预算 | Context 和 Provider 复用请求构造；工具 schema 与协议包装计入预算，压缩后最终复算 | 12 组 `context_cases.json`；`test_context_provider.py` |
| 3. 工具调用/结果配对被切坏 | 按完整交换裁剪，保留推理元数据，checkpoint 不按固定条数截断；中断补齐取消结果 | `test_context_provider.py`、`test_review_regressions.py` 多调用与中断检查 |
| 4. 工具分发不通 | todo/plan/diff 由运行状态入口实现；移除工作区工具中的重复状态处理 | `test_runtime_repairs.py` 的真实路由与 diff；`tool_load` 可发现名称并验证加载 |
| 5. diff 使用哈希冒充原文 | 哈希用于漂移；单独保存任务开始时的文本基线，resume 后沿用 | 修改前空 diff、修改后差异、恢复后差异回归 |
| 6. TUI 自动批准 | 原生 Textual 模态框明确批准或拒绝，异步问答、进度与取消 | `test_interaction_workers.py` 无窗口操作真实组件 |
| 7. 记忆准入无内容约束 | 仅接受当前运行成功 read_file 的摘录与来源哈希；拒绝敏感/临时/越权/无支持内容及来源洗白 | 12 个准入场景、Runtime 来源洗白回归、原 durable_promotion_reject 任务 |
| 8. 回滚污染快照 | 存取快照均深拷贝，回滚不绕过来源新鲜度 | 两个回滚场景、重复回滚计数回归 |
| 9. 成功收尾漏 Stop hook | 成功、失败、取消统一执行一次 Stop；先更新最终工具状态再收尾 | 生命周期 3 场景、取消与后置停止报告回归 |
| 10. 演化反馈不起作用 | 使用反馈改变排序，反复失败/来源变化条目被停用；保留证据与回滚，旧 schema 隔离待复核 | 6 个反馈开关场景、4 个来源变化场景；明确这是固定反馈机制实验 |
| 11. 交互工具占位 | ask_user 接通回调；worker 指导排队、下一轮交付，可取消并回收并发名额；父权限只收窄 | 交互/权限测试；真正 WorkerManager 重改旧路径后工作记忆失效回归 |
| 12. 模型错误缺乏诊断 | 分类脱敏错误，瞬时错误最多三次尝试；非法请求不重试；Chat/Anthropic 修正协议结构 | Provider 模拟传输/429/400、推理字段与图像契约测试；真实模型仅 Responses |
| 13. 专项评测薄弱 | 新增 42 个机制场景、70 次条件尝试；独立脚本生成可追溯对照报告 | `benchmark_mechanisms.py`、`compare_benchmarks.py`、`datasets/` |

工作记忆没有删掉：它只属于 RunState，关闭长期记忆仍可使用。直接工具调用、自主运行及子任务共享同一执行入口。进一步修复了只读/plan 经 Shell 写入、复合 Shell 绕过硬拒绝、Git worktree 的 `.git` 文件误入沙箱、记忆状态符号链接、空测试伪验证与候选 `pytest.py` 冒充评分器。

## 后训练接口验收

- `reset()` 为每次 episode 创建初始仓库副本，不改原始种子仓库，不继承上轮状态或长期记忆。
- 每次外部策略动作消耗一轮；终态不能继续 step，工具成功不产生正奖励。
- Final 前等待子任务退出，冻结候选后再执行宿主 `verifier(root, state)`；成功 +1、失败 −1，没有独立验证器则成功奖励为 0。
- 运行自身的测试结果不等于奖励验证。独立判分、防重复领取奖励、工作区重置等有回归与 4 个数据场景。
- 暂不实现 SFT/GRPO；训练侧仍需设计可信的任务验证器、训练数据与留出评测，并在 reset/close 前保存需要的临时轨迹。

## Ponytail 复查

保留一个 Runtime，没有新增服务、框架依赖、模型裁判或抽象插件系统。使用 asyncio 队列/Task、Textual 模态框、深拷贝和独立临时目录解决已复现问题。删除了工具中的重复状态处理，以及 EvidenceStore 无调用者的三个接口。

有意保留的代价：本轮已读取文件在构建上下文及执行后会重新检查文本哈希，适合轻量仓库，暂不引入 watcher；worker 共享工作区，只接受不冲突的写入范围；多个顶层进程并发修改同一长期记忆文件不是当前支持的运行模式。

## 证据入口

[修复前后比较](../artifacts/benchmark-v3-comparison/report.md)、[确定性回放](../artifacts/benchmark-v3-scripted/report.md)、[机制实验](../artifacts/benchmark-v3-mechanisms/report.md)、[真实模型](../artifacts/benchmark-v3-real/report.md)。原始 v2 失败与 v3 第一批次均保留。

当前固定题目通过不等于任意仓库可靠，也不等于任意恶意 Python 候选无法攻击进程内测试。可以进入小规模后训练方案与 rollout 实验；不宣称分布式训练平台或后训练收益已经完成。
