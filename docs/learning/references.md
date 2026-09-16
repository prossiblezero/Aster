# 教学参考与阅读方式

[返回课程目录](README.md)

本课程参考下面三个公开项目的 Markdown 教学写法，按 Aster 当前源码重新组织内容。阅读日期为 2026-09-16，链接固定到当时的提交，便于之后核对。借鉴的是问题驱动、逐段代码导读、运行实验和边界说明，没有复制一套教学 Agent 或把其他项目的功能说成 Aster 已实现。

## learn-claude-code：每次只引入一个核心机制

仓库：[shareAI-lab/learn-claude-code](https://github.com/shareAI-lab/learn-claude-code)。阅读版本 `0dcafa2ae053a1ddd6a72f265431104b08a5aa13`。

- [Agent 循环](https://github.com/shareAI-lab/learn-claude-code/blob/0dcafa2ae053a1ddd6a72f265431104b08a5aa13/docs/zh/s01-the-agent-loop.md)：先提出模型碰不到工作区的问题，再用一张图和一个循环解释工具反馈。
- [上下文压缩](https://github.com/shareAI-lab/learn-claude-code/blob/0dcafa2ae053a1ddd6a72f265431104b08a5aa13/docs/zh/s06-context-compact.md)：把触发条件、不同压缩层和动手实验放在一起。
- [记忆](https://github.com/shareAI-lab/learn-claude-code/blob/0dcafa2ae053a1ddd6a72f265431104b08a5aa13/s09_memory/README.zh.md)：围绕跨会话信息解释机制和边界。

Aster 沿用“一章解决一个问题”的学习粒度，但不要求每章产生一份递增复制的 Agent 脚本。课程调用现有 Runtime，让读者学到的就是项目实际运行的路径。

## claw0：用流程图讲清状态与失败路径

仓库：[shareAI-lab/claw0](https://github.com/shareAI-lab/claw0)。阅读版本 `0fbf0eb1bd6515fa5b8faf90067b47128df8f45a`。

- [Agent 循环](https://github.com/shareAI-lab/claw0/blob/0fbf0eb1bd6515fa5b8faf90067b47128df8f45a/sessions/zh/s01_agent_loop.md)：用很少的组件建立运行主线。
- [会话与上下文保护](https://github.com/shareAI-lab/claw0/blob/0fbf0eb1bd6515fa5b8faf90067b47128df8f45a/sessions/zh/s03_sessions.md)：把持久化、历史重建和压缩触发串成可跟踪的过程。
- [弹性](https://github.com/shareAI-lab/claw0/blob/0fbf0eb1bd6515fa5b8faf90067b47128df8f45a/sessions/zh/s09_resilience.md)：通过错误分类解释不同恢复层的责任。

Aster 借鉴这种“沿数据流走读”的表达。claw0 对应章节中的 LLM 摘要、凭证轮换、模型故障转移不是 Aster 当前能力；Aster 采用确定性上下文整理和有限请求重试，不能照搬参考项目的功能表。

## learn-workbuddy：字段、契约与离线验证一起教

仓库：[adongwanai/learn-workbuddy](https://github.com/adongwanai/learn-workbuddy)。阅读版本 `53538625e3b0e3f80dd314ac75aaaaa4c7b71901`。

- [学习指南](https://github.com/adongwanai/learn-workbuddy/blob/53538625e3b0e3f80dd314ac75aaaaa4c7b71901/docs/learning-guide.md)：提供不同阅读路线，并把离线与真实模型体验分开。
- [工具延迟加载](https://github.com/adongwanai/learn-workbuddy/blob/53538625e3b0e3f80dd314ac75aaaaa4c7b71901/s03_deferred_loading/README.md)：同时解释设计不变量、字段、失败路径与可复现实验。
- [上下文压缩](https://github.com/adongwanai/learn-workbuddy/blob/53538625e3b0e3f80dd314ac75aaaaa4c7b71901/s14_context_compact/README.md)：将压缩过程、状态保护和具体实验组织成可验证的课程。

Aster 同样为字段补充含义、反例和结果预期，并提供无需密钥的主线。实现细节仍以 Aster 为准，例如 Aster 加载工具后在下一轮展开原始工具定义，没有照搬参考章节的通用 `DeferExecuteTool` 入口。

## 如何使用参考项目继续学习

先把 Aster 一章跑通，再看相近主题的参考章节，比较同一个问题的取舍。例如：有无额外 LLM 总结、是否重写历史、加载工具是否也是执行前置条件、会话怎样恢复。比较应落到数据流与失败边界，而不是“谁的组件名字更多”。

本课程实验复用 Aster 自身代码。出现与旧报告不一致的默认值时，以当前 [配置](../configuration.md) 和源码为准；历史评测仍归属于报告注明的版本，不会因教程更新而自动变成新版本成绩。
