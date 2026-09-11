# 简历草稿（基于当前真实实现与评测）

## Aster：安全、证据驱动的本地 Coding Agent Harness

**核心技术：** Python、Agent Harness、Tool Calling、Pydantic、Landlock/seccomp、Sandbox、Context Engineering、Checkpoint/Resume、Layered Memory、Memory Evolution、Run Trace、RL Environment

**项目描述：** 面向代码仓库长链路任务实现独立本地 coding-agent harness，统一模型接入、结构化工具调用、权限审批、进程隔离、上下文治理、验证、记忆演化、运行审计和 Agent-RL 环境；重点解决多轮任务中的工具副作用不可控、过期验证证据、状态漂移和轨迹难复盘问题。

**核心职责与贡献：**

1. **统一 Runtime 与执行边界：** 设计 Run/Turn/Step/ToolCall 数据契约，交互 loop 与 RL `step` 共用同一 ToolExecutor；支持 Responses、Chat Completions、Anthropic-compatible 三类 provider、25 个结构化工具 schema 和 hash-linked JSONL trace。
2. **工具安全与隔离：** 实现 deny-first 权限、approval、fresh-read、工作区 canonical path、symlink/敏感路径/控制目录保护；Linux 下结合 Landlock + seccomp、环境白名单、资源上限和临时副本原子回写，`required` 后端不可用时 fail-closed。
3. **长上下文与完成治理：** 实现 token-aware budget、输出预留、失败/修改证据保留和 compact 报告；通过 verifier 使“验证通过后再次修改”自动失效，防止旧证据误导最终结论。
4. **恢复与记忆：** 融合有界 stall detection/recovery、checkpoint workspace drift 拒绝恢复、working/durable/evolution 分层记忆；lesson 必须绑定 run/evidence provenance，并支持使用反馈与回滚。
5. **RL 环境接口：** 提供 `reset/step`、结构化 reward、terminated/truncated/info、轨迹与安全事件，不绕过生产权限、sandbox、verifier 和 completion gate；暂未实现 SFT/GRPO。
6. **量化评测：** 固定 Harness 回归覆盖路径/控制目录/敏感文件、真实隔离、编辑验证、RL 共用边界、context compact、memory provenance/rollback 和 RL wire action；当前 deterministic suite **10/10 通过（100%）**，另以真实 `gpt-5.6-luna` smoke 验证模型 tool-calling 与 trace 落盘。

> 当前 100% 是确定性 Harness 契约通过率，不是模型任务成功率；后续 SFT/GRPO 完成并有真实对照后再补写后训练成果。
