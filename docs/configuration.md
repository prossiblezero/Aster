# 配置与字段说明

从项目根目录运行 `conda activate aster`。CLI 用 `--config aster.toml` 读取 TOML，用 `--env-file .env` 指定模型连接配置；没有指定后者时读取**启动命令所在目录**的 `.env`，并非 `--cwd` 指向的任务仓库。

配置优先级是：显式 CLI 参数 > 进程环境变量 > `.env` > TOML > [Settings 默认值](../src/aster/config.py)。当前只读取四个 `ASTER_*` 环境变量：`ASTER_PROTOCOL`、`ASTER_MODEL`、`ASTER_BASE_URL`、`ASTER_API_KEY`。其余设置通过 TOML 或 Python 的 `Settings(...)` 传入；没有 `ASTER_MAX_WORKERS` 之类的隐含开关。

## 模型与输入预算

| 字段 | 默认值 | 含义与边界 |
|---|---|---|
| `protocol` | `responses` | 请求格式；另有 `chat`、`anthropic`。真实模型评测使用 Responses；后两者验证了请求与响应契约，尚不代表真实服务验证 |
| `model` | `gpt-5.6-luna` | 网关识别的模型名称 |
| `base_url` | `https://api.openai.com/v1` | API 根地址，不附加 `/responses` 等具体接口路径 |
| `api_key` | 空 | 连接凭证；建议放在未提交的 `.env` 或进程环境，禁止写入已提交配置 |
| `request_timeout` | `90` | 一次 HTTP 请求的超时秒数；瞬时错误最多尝试三次，因此整个模型轮次可能超过此值 |
| `context_tokens` | `24000` | 本地估算的总上下文容量，包含输入与输出预留；不是全部都可用于输入 |
| `output_tokens` | `2048` | 输出预留，也是供应商请求的最大输出长度 |
| `tool_output_tokens` | `1600` | 单条工具结果的正文预算；Runtime 结果保留状态、原因、证据编号与修改路径，这些元数据仍计入全局输入预算 |
| `tool_profile` | `core` | 首轮展示可用核心工具，其他工具由 `tool_load` 按需加入；`all` 展示当前配置可用的全部工具 |

例如 `context_tokens=4096`、`output_tokens=512`，实际输入上限就是 `3584`。`ContextBuilder` 使用与 Provider 相同的请求构造函数，把工具名称、说明、参数 schema、消息和协议包装一并计数。无法保留当前任务及必需交互时，返回 `context_budget_exceeded`，不继续发送已知超预算请求。

这里的 token 来自本地 tokenizer，对远端模型仍是估算。图片编码、供应商内部模板和计费规则可能不同；费用与真实 usage 以网关返回结果为准。详见 [阶段 13](learning/13-runtime-reliability.md)。

`todo_list` 与 `skill_load` 使用字符分页：`offset` 从 `0` 开始，`limit` 默认 `512`、范围 `1–1000`；结果正文第一行给出 `next_offset` 和 `total`。正文预算不足时会自动缩小页面，因此后续调用必须使用返回的 `next_offset`，不能直接加上请求的 `limit`。`next_offset=null` 表示到达当前文本末尾；`total` 是原文字符数。`skill_load` 还需保留技能 `name`。

普通工具的完整结果保留在 trace；分页工具返回当前页，完整计划仍在运行状态中，完整技能仍在原文件中。自动摘要被截断时可以继续翻页，选中技能不等于看完全文。正文与元数据的 JSON 结构、中文分页示例见 [阶段 15](learning/15-paged-observations.md)。

## 权限与执行范围

| 字段 | 默认值 | 含义与边界 |
|---|---|---|
| `approval` | `ask` | `ask` 需要真实用户批准；没有回调就拒绝。`auto` 允许策略内的常规写入或执行；显式 `ask` 规则仍会询问。`never` 不提供人工批准，拒绝仍需审批的动作 |
| `sandbox` | `required` | `required` 要求隔离可用；`best_effort` 仅在没有只读/写入范围约束时可记录降级并直接运行；`off` 关闭隔离，也不能执行需要隔离保证的只读或范围约束命令 |
| `network` | `false` | 隔离子进程是否可以联网；不控制 Harness 自身调用模型 API 的网络 |
| `read_only` | `false` | 阻止写工具；Shell 在隔离副本内运行且不能把修改带回工作区 |
| `write_scope` | `None` | 工作区内允许写入的相对路径前缀。`None` 不额外收窄；`[]` 不允许写任何文件；`["src", "tests"]` 仅允许这两个路径及其后代 |
| `rules` | `[]` | 命令参数前缀规则；字段解释见下文 |
| `max_workers` | `2` | 同时运行的子任务数；已完成子任务不继续占用名额。子任务内部固定为 `0`，不能递归扩张 |

`approval=auto` 无法覆盖工作区越界、只读、禁止规则或 Hooks 拒绝。TUI 的 **Allow once** 只批准当前动作，不修改配置。`plan_enter` 进入计划模式后，写工具被拒绝，Shell 只能在副本中观察；`plan_exit` 仍经过权限判断。

工具目录与执行入口使用同一份能力判断：关闭持久记忆、禁用 Worker、无法询问用户、只读/计划模式或关闭所需沙箱时，不展示对应不可用工具。`tool_load` 的名称目录也同步收窄；直接提交动作仍会再次校验。文件编辑前仍要求最新读取证据，这属于动作前置条件，不会永久隐藏写工具。

没有审批回调时，只展示能由现有允许规则授权的执行工具，并继续尊重更高优先级的拒绝。例如只允许 `echo` 时可展示 `run_shell`，不会因此展示 `run_tests`；允许 `python -m pytest` 才能授权对应测试命令。之后接入或移除审批回调，目录随之更新。

沙箱复制任务文件，排除 `.git` 等控制元数据。因此即使原仓库有 Git，副本中也不能依赖 `git status`；`git_diff` 展示 Aster 保存的初始文本及权限模式与当前文件的差异。禁网保留匿名本地 IPC，支持 asyncio 与线程唤醒；仍拒绝外部网络和命名 Unix socket。文件回写逐文件原子，批次失败保留并报告已经写入的路径；不覆盖外部编辑做回滚。当前支持同一进程内的 Worker，并不承诺多个 Aster 进程同时写同仓库的事务隔离。

每条 `ExecRule` 包含：`prefix` 是切分后的命令参数前缀，例如 `["git", "status"]`；`decision` 为 `allow / ask / deny`；`reason` 是给人和日志看的原因；`match / not_match` 是可选的配置自检示例，不是额外匹配算法。多个前缀命中时，优先级为 `deny > ask > allow`。

命令分类区分引用参数和实际 Shell 语法，例如 `python -c "print(1); print(2)"` 中的分号属于 Python 参数。它仍可执行任意代码，安全边界由审批与沙箱保证。实际管道、重定向、替换、已知 Shell 包装器等需要单次批准；存在任何 `deny` 规则时保守拒绝。无路径的 deny 命令名也匹配绝对路径，例如 `rm` 同时拒绝 `/bin/rm`。前缀规则不分析任意脚本内部的程序行为，不是传递到所有子程序的静态安全证明。

无人运行代码评测时可以使用 `sandbox="required", approval="auto", network=false`，让受隔离和范围约束的动作自动执行。选择 `approval="never"` 时，要接受写入/执行被拒绝，或明确配置允许的命令前缀；不能把它当作“关闭审批后全部放行”。

## 运行、记忆与完成条件

| 字段 | 默认值 | 含义与边界 |
|---|---|---|
| `max_recovery` | `1` | 检测到重复无进展后，最多给多少次改变方案的机会；用尽后停止 |
| `max_final_revisions` | `2` | 模型准备结束但尚未满足任务条件时，最多要求它补做多少轮 |
| `completion` | `strict` | `strict` 不满足完成条件就停止；`warn` 允许带警告完成；`soft` 当前与 `warn` 等价；`off` 不要求补做，但仍输出验证结果 |
| `memory_enabled` | `true` | 开关跨运行记忆的保存和召回；**不关闭会话内 working memory** |
| `memory_evolution` | `true` | 开关使用反馈、基于反馈的排序及 Dream；关闭后仍可保存合格摘录，来源失效和安全准入仍生效 |
| `auto_dream` | `true` | 每次运行结束后自动执行一次记忆维护：标记过期、被否定或反复失败的条目；不是调用另一个模型写总结 |

以下属于 [TaskSpec](../src/aster/schema.py)，不是全局 Settings：

| 字段 | 默认值 | 含义 |
|---|---|---|
| `prompt` | 必填 | 当前任务请求 |
| `task_id` | 自动生成 | 任务标识；一题多次尝试可以共用任务标识，但每次运行有不同 `run_id` |
| `max_steps` | `40` | 最多执行多少次工具动作 |
| `max_turns` | `40` | 最多进行多少次模型决策；RL 外部策略的每次 `step`，包括 `Final`，都计一轮 |
| `require_observation` | `false` | 完成前是否必须观察工作区 |
| `require_edit` | `false` | 完成前是否必须产生文件修改 |
| `require_verification` | `false` | 完成前是否必须在当前工作区上取得有效测试结果 |
| `required_artifacts` | `[]` | 完成时必须存在的文件路径 |

Runtime 只对未显式设置的条件做文字推断；显式 `False` 会保留。正式评测应在任务数据中明确这些条件。`require_edit` 表示本轮发生过修改，目标代码是否正确仍由独立验证器判断。内容或权限位变化会使旧验证失效；checkpoint 指纹现在包含文件权限位，因此旧版本的指纹可能被拒绝恢复，不能将其当作当前有效状态。用于 RL 奖励的独立验证器另行接入，不能用 `completion="off"` 关闭它，见 [阶段 14](learning/14-memory-and-rollouts.md)。

同一 Runtime 同时只接受一个运行操作，单个 RL episode 也只允许一个在途 `step`；并发调用明确返回 busy。idle 状态重新 `start` 会以 `superseded` 结束旧任务，`resume` 不会覆盖活动任务。终态自动汇总当前任务 Worker 的修改，旧任务的 Worker ID 不能污染新任务；取消后迟到的审批或模型返回不能继续写文件。

一个只读浏览配置可以写成：

```toml
# browse.toml
approval = "never"
sandbox = "required"
read_only = true
network = false
context_tokens = 24000
output_tokens = 2048
tool_profile = "core"
```

```bash
aster --config browse.toml --cwd examples/demo '查看仓库并说明入口'
```

检查代码中的实际行为：

```bash
python -m pytest -q tests/test_new_contracts.py tests/test_safety.py tests/test_interaction_workers.py
```

## 内建测试的语言范围

`run_tests` 当前接受 `pytest`、`python -m pytest` 和 `python -m unittest`，使用结构化结果形成验证证据。其他命令可经 `run_shell` 执行，但普通 Shell 成功不会自动变成测试通过证明。当前内建完成验证与题库面向 Python；其他语言应接入对应验证器，不能把任意命令退出零当作验证成功。
