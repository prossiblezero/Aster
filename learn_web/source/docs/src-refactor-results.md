# src 布局重构结果

日期：2026-09-19。实施依据：[src-refactor-plan.md](src-refactor-plan.md)。

## 版本与范围

起点是完整交接标签 `pre-src-refactor-20260919`（`bb8deb4`），其代码基线为 `f85cb1c`。开始时仓库干净，在原工作区 `/home/dzh/Aster` 创建 `refactor/src-layout`。未搬运旧 WIP，未发现新的并行修改；既有其他 worktree 未改动。四批通过验证后分别提交，完成后将本地 `master` 快进到结果分支；保留标签与结果分支，不推送远程。

| 批次 | 提交 | 交付 |
| --- | --- | --- |
| 工具与调度 | `3fa6170` | `core/`、`tools/`；明确工具依赖、复用参数校验结果、抽离只读调度 |
| 协议与历史 | `f4e7a81` | `providers/`、`messages.py`；纯 codec 与每请求记账 |
| 会话与上下文 | `da23241` | `sessions/`、`context/`；候选准备、成功后提交 |
| UI 与最终验收 | 本报告所在的 `refactor(ui)` 提交 | `ui/`、文档/网站、安装验证，以及复核发现的直接入口错误文字兼容修正 |

第四批的精确提交号可用 `git log --oneline pre-src-refactor-20260919..refactor/src-layout` 查看，避免在提交内部写入自身哈希。

本轮新增生产依赖 **0**、新增用户命令 **0**。保留 25 个命令、中文界面、Aster 艺术字、流式文字与接口返回的思考摘要。`pyproject.toml`、配置默认值、公共 schema、RL 动作/奖励接口和 Linux 沙箱实现未变；没有修改 `exam_project`。未实现会话树、全局配置发现或其他新功能。

## 代码指标与职责

指标由标签源码与最终 `src/aster/**/*.py` 计算，物理行包含注释和空行；最长函数按 AST 的起止行计算。

| 指标 | 重构前 | 重构后 |
| --- | ---: | ---: |
| Python 模块数 | 27 | 51 |
| 全部源码物理行 | 9,350 | 9,707 |
| Runtime 实现 | 1,613 | 1,362（`core/runtime.py`） |
| ModelClient 所在实现文件 | 939 | 166（`providers/client.py`） |
| TUI App 所在实现文件 | 854 | 616（`ui/app.py`） |
| Context 入口实现文件 | 458 | 47（`context/builder.py`） |
| 最长函数 | `ContextBuilder.build`，327 | `BudgetCalculator.calculate`，244 |

这些入口长度不能代表其全部业务减少：新 `context/` 合计 533 行，`ui/` 合计 929 行，`providers/` 合计 1,256 行（原 Provider 加 Streaming 为 1,311 行，另有历史逻辑移入 messages）。总行数增加 357（约 3.8%），来自显式导入/兼容出口、拆出的函数签名和窄回调、候选结果与请求记账对象。新增的三项测试不计入上述源码指标；没有新增业务功能。这里的收益是职责和状态归属清楚，并非总行数下降。

- `core/runtime.py` 持有当前 RunState，控制授权、检查点时序和结果归集；`core/execution.py` 只调度已准备的读动作，并等待取消后的线程收束。
- `tools/schemas.py` 集中参数与模型可见描述；registry 负责直接校验和分发；workspace/workflow/testing 分别承担工作区、串行工作流和正式测试业务。ToolContext 不再持有 Runtime，Shell 接收明确的只读状态。
- `messages.py` 只依赖标准库；`providers/codec.py` 统一实际发送与预算计数的协议转换。client/streaming 共享 errors 的重试规则以及 usage 的 RequestAccounting；SSE 不持有 ModelClient。
- `sessions/checkpoint.py` 保留写入版本 2 和加载版本 1/2 的身份、历史、inflight、Worker 与归档来源检查。lifecycle 准备续聊/模型切换候选，Runtime 在预检成功后安装。
- `context/prompt.py` 保留策略原文和消息顺序；budget 计算候选裁剪结果，builder 在成功后统一更新输入历史与消息。归档仍先于裁剪，预算失败不发布裁剪结果。
- `ui/commands.py` 接收明确的 Runtime、展示值和 UI 回调；widgets 保留输入和批准框，app 持有界面状态与 Textual 事件。没有共享整个 App 私有字段的 mixin。

旧 `runtime.py`、`provider.py`、`history.py`、`session.py`、`testing.py`、`tui.py` 均只有显式导出，无第二份业务实现；11 组实际使用的公开符号已核对对象身份。`context.py`、`tools.py` 被同名包替代，内部 `streaming.py` 迁入 providers。私有 parser/usage、monkeypatch 和网站实验传输器同步迁移。单独导入 messages、codec、context 和 sessions 均不会加载 HTTP 客户端、Runtime 或 Textual。

## 确认删除的重复逻辑

1. 模型批次原来在 `_execute_batch`、`_prepare_tool` 和具体工具入口最多进行三次业务参数解析；现在整批预检产生规范化结果，由授权和执行复用。直接入口仍独立校验，审批与 hook 仍接收深拷贝；没有删除审批后原始文件字节检查。新增测试确认模型路径解析一次，以及直接调用仍拒绝坏参数。
2. `memory_retrieve` 原来在 workflow 分支与 `_collect_step` 两次合并 `recalled_ids`，现在由 `_collect_step` 归集一次。自动请求召回的 `_memory_text` 是另一条实际召回路径，保留其记账。
3. 流式与非流式的累计 usage、最新诊断统一放入每次 `complete()` 的 RequestAccounting，成功、失败、取消均通过 finally 发布。共同的可重试 HTTP 状态、空响应重试限制和退避计算只定义一次；原有两类超时语义保持不变。
4. 模型切换与跨模型恢复共用 `prepare_model_history`。上下文预检不再临时替换 `Runtime.state` 来计算工具可用性，而是传入候选状态。

复核发现并修正了直接 ToolRegistry 的未知工具提示丢失：直接调用继续返回 `unknown tool <name>`，Runtime 批次预检继续保持原有空 content。对应测试同时覆盖两个边界。

## 验证结果

所有 Python、pip、pytest 和构建均来自 Conda `aster` 或它的完整临时克隆；没有使用 base Python，也未重指共享环境的 editable 安装。验证目录为 `/tmp/aster-refactor-check.z0ld4xha/`：源码验证使用 `conda/bin/python`，wheel 验证使用 `wheel-env/bin/python`。

最初按方案尝试的普通 venv，其 Python 符号链接指向父 Conda，而现有 Landlock 授权基于当前前缀，造成基线工具报 `python: Permission denied`（123 failed / 859 passed）。原 Conda 的定向对照通过后，改用完整 Conda 克隆，重新确认源码导入位置，基线 982 项全部通过。未放宽沙箱或改变产品行为来适配测试环境。

| 验证 | 最终结果 / 退出状态 |
| --- | --- |
| 基线 `python -m pytest -q` | 982 passed，54.91 秒；0 |
| `python -m ruff check src tests scripts learn_web/scripts learn_web/tests` | 通过；0 |
| 第一批工具/调度及相关能力回归 | 249 passed；0。补充工具与网站检查 109 passed；0（有重叠，不累加） |
| 第二批协议/历史/usage/流式回归 | 262 passed；0；网站 25 passed；0 |
| 第三批会话/上下文/训练契约回归 | 279 passed；0；新增计算边界所在文件 8 passed；0 |
| 第四批 CLI/TUI/安装契约回归 | 65 passed；0 |
| 复核后的最终 `python -m pytest -q` | **985 passed，52.75 秒；0**，无跳过 |
| `python docs/learning/run_examples.py all` | **17 examples passed；0** |
| `python learn_web/build.py` | 成功；0，清理并重建源码镜像 |
| 重建后 `python -m pytest -q learn_web/tests` | **25 passed；0**（已含在完整回归中） |
| 固定输入前后对照 | 正常任务、末尾坏参数批次、写入后服务失败再恢复、预算压缩、模型切换、流式失败/取消/重试，标签、当前源码和 wheel 结果一致；cmp 为 0 |
| wheel 构建和独立安装 | pip wheel/install 均为 0；51 个 Python 模块完整，含 `safety/linux.py`，逐文件字节与最终源码一致，无缓存或私有数据 |
| 实际 CLI / PTY | `aster --help`、`aster-tui --help`、裸启动、本地命令、流式/摘要展示、取消、quit 均成功；正常退出 0；脚本无任务按契约退出 2 |
| 安装版本失败与恢复 | CLI 5 次内存模型替身调用；工具读取失败可观察，成功写入后 provider 失败，resume 后完成；写入仅一次，session_id 保持，continued_from 正确 |
| 架构、安全及四方向对抗复核 | 一处直接入口说明文字差异已修正；无未解决发现 |

固定输入对照保留协议字段、工具描述/schema 顺序、工具参数、调用/结果顺序、错误原因、缓存与已知用量；仅排除流事件实际耗时等非确定字段。预算压缩与流式对照结果见临时目录 `baseline-equivalence.json`、`final-equivalence.json`、`installed-equivalence.json`，任务对照见 `*-contract.json`。其他日志有 `final-tests-reviewed.log`、`final-examples.log`、`installed-recovery.log`、`terminal-results.json`、`bare.pty`、`cancel.pty` 和 `final-metrics.json`。

wheel 实际导入路径为：

```text
/tmp/aster-refactor-check.z0ld4xha/wheel-env/lib/python3.12/site-packages/aster/
  core/runtime.py
  providers/client.py
  context/builder.py
  ui/app.py
```

CLI 的声明入口仍为 `aster.cli:main` / `aster.cli:tui_main`；实机验证在临时工作区执行。裸打开界面与本地命令不生成任务；受控流式 PTY 只调用一次替身。所有模型响应均来自离线替身或 MockTransport，没有请求真实模型 API。

## 剩余边界

会话仍是现有的线性续聊与运行检查点，不是 pi 会话树。后续树导航可在 `sessions/lifecycle.py` 准备候选，再由 Runtime 空闲门禁安装；本轮没有新增 `/tree` 或存储格式。归档可能在预算预检期间写入，但失败时不安装候选状态，这与原有时序一致。

Runtime 仍协调权限、证据、Worker 和生命周期；244 行预算计算、208 行 SSE 状态处理、187 行正式测试运行仍保留复杂边界。没有引入通用状态增量框架，也未统一不同传输的超时语义。训练数据/动作/奖励契约继续可用，但本轮不交付训练器，不宣称真实任务成功率、缓存或 Memory/Skills 收益提升。验证覆盖离线等价与当前 Linux 终端；不代表对真实 provider 或其他操作系统的实机认证。
