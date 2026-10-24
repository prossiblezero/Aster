# 模块整理与导入迁移

此页记录接续 `c7a837d` 的职责拆分与导入迁移。当次将 `src/aster` 顶层收敛为 `__init__.py`、`cli.py`、`config.py`、`schema.py`、`messages.py`。后续 MCP 接入新增文件服务入口 `mcp_files.py` 和服务熔断模块 `resilience.py`；当前顶层共七个 Python 文件。

## 职责与依赖

| 目录 | 内容 |
| --- | --- |
| `core/` | Runtime、只读并行调度、Hooks、恢复、完成验证、Worker 协调 |
| `providers/` | 协议编码、模型传输、流式处理、错误和请求用量 |
| `context/` | 提示区段、预算计算、成功后的历史提交 |
| `sessions/` | 检查点编码/校验、续聊和模型切换候选 |
| `tools/` | 参数 schema、工具分发、工作区/工作流业务、MCP 客户端、正式测试、可用性规则 |
| `storage/` | 工作区边界、证据和观察归档、持久化调用回执 |
| `knowledge/` | 项目持久记忆、Skills 加载与快照 |
| `safety/` | 脱敏、权限、沙箱执行、Linux 隔离 |
| `training/` | 外部策略环境、SFT/GRPO 数据与校验契约 |
| `ui/` | Textual App、本地命令、输入与审批组件 |

`Redactor` 位于 `safety/redaction.py`，仅依赖标准库。Provider 使用脱敏无需导入证据存储。工具可用性由 `tools/availability.py` 接收 Settings、候选 RunState 及是否具备交互回调，供 Runtime 的展示、加载及执行预检调用；实际动作仍执行参数权限与审批检查。Worker 保留创建子 Runtime 的真实关系，没有新增工厂或依赖注入容器。

## Python 导入变更

职责拆分时删除了旧导入路径，外部 Python 调用者按下表更新 import。Agent 命令入口为 `aster.cli:main` 和 `aster.cli:tui_main`，MCP 文件服务入口为 `aster.mcp_files:main`。

| 原路径 | 当前实现 |
| --- | --- |
| `aster.runtime.Runtime` | `aster.core.runtime.Runtime` |
| `aster.hooks` / `recovery` / `verifier` / `workers` | `aster.core` 下同名模块 |
| `aster.workspace` / `evidence` | `aster.storage` 下同名模块 |
| `aster.memory` / `skills` | `aster.knowledge` 下同名模块 |
| `aster.evidence.Redactor` | `aster.safety.redaction.Redactor` |
| `aster.rl.AsterEnv` | `aster.training.env.AsterEnv` |
| `aster.training` 中的数据类型与校验函数 | `aster.training.contracts` |
| `aster.provider.ModelClient` | `aster.providers.client.ModelClient` |
| `aster.provider.ProviderError` | `aster.providers.errors.ProviderError` |
| `aster.provider.request_payload` | `aster.providers.codec.request_payload` |
| `aster.provider.ContextHistoryError` / `history_groups` | `aster.messages` 中同名对象 |
| `aster.history` | `aster.messages` 中的 `CanonicalHistory` / `canonicalize` |
| `aster.session` 中的检查点与身份校验 | `aster.sessions.checkpoint` |
| `aster.session` 中的归档、续聊与上下文预览 | `aster.sessions.lifecycle` |
| `aster.testing` | `aster.tools.testing` |
| `aster.tui.AsterTui` / `ASTER_MARK` | `aster.ui.app` |
| `aster.tui.COMMANDS` | `aster.ui.commands.COMMANDS` |
| `aster.tui.HumanPrompt` / `PromptInput` | `aster.ui.widgets` |

当前 README、教程、工程脚本和测试直接使用实现路径，monkeypatch 也指向实际依赖所在模块。冻结的历史方案、评测日志及旧提交报告保留原路径；`exam_project` 不参与迁移。

## 指标与删减

统计范围为 `src/aster/**/*.py`，行数含注释与空行，基线为 `c7a837d`。本轮重点是归类和依赖边界，没有把移动行数计作删除。

| 指标 | 整理前 | 整理后 |
| --- | ---: | ---: |
| 顶层 Python 文件 | 21 | 5 |
| 全包 Python 模块（含 `__init__`） | 51 | 50 |
| 全包物理行数 | 9707 | 9704 |
| `core/runtime.py` | 1362 | 1306 |
| `providers/client.py` | 166 | 166 |
| `ui/app.py` | 616 | 616 |
| `context/builder.py` | 47 | 47 |
| 最长函数 `context.budget.calculate` | 244 | 244 |

删除了 `runtime.py`、`provider.py`、`history.py`、`session.py`、`testing.py`、`tui.py` 六个兼容出口。`tool_load` 对不可用工具的相同判断从两次合并为一次；没有删除参数校验、授权检查、恢复预检等多层防线。脱敏类与可用性判断各只有一份实现，分别解除 Provider 对证据模块、Runtime 对具体工具规则的直接依赖。


## 开发验证

当前验证命令见[开发指南](development.md)，历史拆分过程与取舍见[优化历程](optimization-history.md)。验证 wheel 时必须离开源码目录，确认导入来自安装环境。
