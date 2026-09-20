# 模块整理与导入迁移

本轮接续 `c7a837d`，把此前仍散落在 `src/aster` 顶层的实现按职责归包。顶层只保留 `__init__.py`、`cli.py`、`config.py`、`schema.py`、`messages.py`。不增加依赖、命令或会话树，不改变 checkpoint、模型协议、工具动作及训练数据/奖励契约。

## 职责与依赖

| 目录 | 内容 |
| --- | --- |
| `core/` | Runtime、只读并行调度、Hooks、恢复、完成验证、Worker 协调 |
| `providers/` | 协议编码、模型传输、流式处理、错误和请求用量 |
| `context/` | 提示区段、预算计算、成功后的历史提交 |
| `sessions/` | 检查点编码/校验、续聊和模型切换候选 |
| `tools/` | 参数 schema、工具分发、工作区/工作流业务、正式测试、可用性规则 |
| `storage/` | 工作区边界、证据和观察归档 |
| `knowledge/` | 项目持久记忆、Skills 加载与快照 |
| `safety/` | 脱敏、权限、沙箱执行、Linux 隔离 |
| `training/` | 外部策略环境、SFT/GRPO 数据与校验契约 |
| `ui/` | Textual App、本地命令、输入与审批组件 |

`Redactor` 位于 `safety/redaction.py`，仅依赖标准库。Provider 使用脱敏无需导入证据存储。工具可用性由 `tools/availability.py` 接收 Settings、候选 RunState 及是否具备交互回调，供 Runtime 的展示、加载及执行预检调用；实际动作仍执行参数权限与审批检查。Worker 保留创建子 Runtime 的真实关系，没有新增工厂或依赖注入容器。

## Python 导入变更

本轮按已批准的方案删除旧导入路径，不保留兼容文件、动态代理或 `sys.modules` 别名。外部 Python 调用者需要更新 import；类及公开方法的行为保留。命令行仍是 `aster.cli:main` 和 `aster.cli:tui_main`。

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

## 验证与交付记录

在由 Conda `aster` 派生的完整克隆环境验证，每次先断言实际导入路径。未使用 base Python；未重指共享环境的 editable 安装。分批记录：

- `b49c113`：存储、知识模块与脱敏迁移；相关测试 282 项通过，网站测试 25 项通过。
- `9d8b70f`：协调与训练模块迁移、工具可用性抽取；相关测试 274 项通过，教程 17 个实验、网站 25 项测试通过。
- 第三批提交主题 `refactor(layout): remove legacy module shims`：删除旧出口，迁移调用者，完成下列整体检查。

| 检查 | 结果 |
| --- | --- |
| `python -m ruff check src tests scripts learn_web/scripts learn_web/tests` | 退出 0 |
| `python -m pytest -q` | 985 passed，退出 0，无跳过 |
| `python docs/learning/run_examples.py all` | 17 examples passed，退出 0 |
| `python learn_web/build.py` / `python -m pytest -q learn_web/tests` | 构建退出 0；25 passed，退出 0（也包含在全量测试中） |
| 迁移 AST 与源码镜像检查 | 10 个迁移模块去掉 import 后正文相同；Redactor 类正文相同；全部 50 个当前模块有对应源码页，旧页面已清除 |
| 固定模型离线对照 | 正常任务、坏参数批次、失败后恢复、流式失败/重试/取消、预算压缩与模型切换结果，与基线逐项一致；源码与已安装 wheel 均通过 |
| wheel 构建/安装 | `pip wheel --no-deps --no-build-isolation` 和独立 Conda 克隆安装均退出 0；50 个 Python 模块与源码一致，包含 Linux 沙箱，不含旧出口/缓存/私有数据 |
| 真实 PTY | `aster` 裸启动；`/context`、`/help`、`quit`；流式文本、思考摘要、取消落盘及退出均通过；裸启动未创建任务，流式仅调用替身 1 次 |
| 已安装 CLI | 两个入口 `--help` 退出 0；非交互无任务退出 2；工具失败→读取→写入→服务失败→恢复，模型替身共 5 次调用，写入仅 1 次 |
| 审查 | 架构、安全及假设/组合/级联/滥用四个角度未发现新增问题；可用性对照覆盖动态设置、候选状态和交互回调 |

验证环境位于 `/tmp/aster-layout-check-20260920/conda`，实际源码位于独立 worktree `/tmp/aster-layout-worktree-20260920/src/aster`。wheel 使用另一个完整 Conda 克隆，实际导入路径为 `/tmp/aster-layout-check-20260920/wheel-env/lib/python3.12/site-packages/aster`，已断言未加载 editable 源码。日志及前后对照 JSON 位于 `/tmp/aster-layout-check-20260920/`；它们是本机临时验证证据，不是项目运行依赖。

全量检查曾发现网站测试仍指向已删除的兼容页，现已更新为实现页，并断言顶层恰有五个源码镜像。没有放宽原有功能测试。未修改配置默认值、共享 schema、Linux/权限规则、生产依赖或历史数据。

本次不证明真实模型任务成功率、Memory/Skills 收益或训练效果有所提升。Runtime 仍承担运行状态安装和时序协调；较长的预算/流式函数保留原有边界。会话仍是 checkpoint 加 `session_id` / `continued_from` 的续接模型，没有分支会话树。训练器、优化器及权重更新仍未实现。

后续会话功能已扩展为类型化记录树；本文件的“没有会话树”描述是上述目录重构交付时的边界。当前用法与持久化设计见[会话管理](sessions.md)。
