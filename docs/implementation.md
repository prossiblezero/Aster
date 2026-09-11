# 实施记录与验收契约

Aster 是独立重构，不导入 pico/nexa 包。主体能力来自 pico-v3 的需求分析，
融合 nexa 的有界恢复、token-aware 预算及带证据的记忆演化。
不实现 SFT/GRPO；实现共用生产执行路径的 RL 接口。

## 工程决策

- Python 3.12 / conda `aster`；`src/aster` 布局；异步模型与进程 I/O。
- Pydantic 负责边界 schema；stdlib 负责持久化、文件与策略；httpx 负责供应商协议；
  Textual 负责终端 UI；tiktoken 用于明确标记的近似计数。
- 一个 Runtime transition 供自主 loop、交互与 RL 调用。事件流与 prompt history 分离。
- 子进程默认禁止网络；安全后端不可用时 required 拒绝。
- 当前宿主 bubblewrap 不能建立 uid/network namespace。使用 Linux Landlock + seccomp
  的独立执行进程与过滤工作区副本作为可验证替代；不得静默裸跑。
- `.aster` 中的账本和策略不暴露给模型直接写；通过受限 memory/plan/todo 工具维护。
- 真实模型固定 `gpt-5.6-luna`；本地密钥从用户授权的 nexa `.env` 导入，永不提交。

## 验收清单

- [ ] Provider：Responses、Chat Completions、Anthropic 适配；原生 tools、usage、取消、错误
- [ ] 工具：文件/搜索/patch/shell/tests/diff/image；统一校验、审批、审计
- [ ] 安全：路径、敏感文件、控制目录、进程/网络隔离、最小权限、子进程取消
- [ ] Runtime：状态、hooks、停滞、恢复、完成契约、验证失效、会话续接
- [ ] Context：最终预算检查、近期/错误/修改证据保留、compact 与报告
- [ ] Memory：working/durable/evolution、索引、增量 Dream、证据来源、版本回滚
- [ ] 工作流：plan/todo/clarification、skills、受限 worker 生命周期、CLI/TUI、多模态
- [ ] 持久化：JSONL trace、checkpoint、report、漂移识别、脱敏
- [ ] RL：reset/step、独立 verifier、终止与截断、轨迹、生产一致性
- [ ] 评测：固定任务、对照、真实 Luna、错误样本、量化报告、复现命令
- [ ] 文档：逐阶段源码导读、字段解释、可运行示例、评测边界、简历精简/详细版

## 指标原则

确定性边界测试不冒充模型任务成功率；测试通过不冒充真实隔离。
报告必须列出分母、任务、模型、配置、重复次数、对照和失败；不复制旧项目数字。
记忆的使用相关性不冒充因果贡献；随机种子不保证远程模型可确定复现。
