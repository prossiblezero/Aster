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
| `tool_output_tokens` | `1600` | 单条工具结果送入模型前的文字上限。工具执行和完整 trace 不按这个上限裁剪 |
| `tool_profile` | `core` | 首轮展示核心工具，其他工具由 `tool_load` 按需加入；`all` 展示全部工具 |

例如 `context_tokens=4096`、`output_tokens=512`，实际输入上限就是 `3584`。`ContextBuilder` 使用与 Provider 相同的请求构造函数，把工具名称、说明、参数 schema、消息和协议包装一并计数。无法保留当前任务及必需交互时，返回 `context_budget_exceeded`，不继续发送已知超预算请求。

这里的 token 来自本地 tokenizer，对远端模型仍是估算。图片编码、供应商内部模板和计费规则可能不同；费用与真实 usage 以网关返回结果为准。详见 [阶段 13](learning/13-runtime-reliability.md)。

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

每条 `ExecRule` 包含：`prefix` 是切分后的命令参数前缀，例如 `["git", "status"]`；`decision` 为 `allow / ask / deny`；`reason` 是给人和日志看的原因；`match / not_match` 是可选的配置自检示例，不是额外匹配算法。多个前缀命中时，优先级为 `deny > ask > allow`。

前缀规则不是 Shell 解释器。若命令包含 `;`、管道、重定向、变量替换等复杂语法，并且配置存在任何 `deny` 规则，Aster 会保守拒绝整条命令，原因是 `compound_shell_with_deny_rules`；否则要求单次批准。这会拒绝一部分无害的复合命令，但不会让一次批准绕过已配置的禁止规则。

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

Runtime 会从任务文字做保守的完成条件推断；正式评测应在任务数据中明确这些条件，避免只靠自然语言猜测。修改会使旧验证失效。用于 RL 奖励的独立验证器另行接入，不能用 `completion="off"` 关闭它，见 [阶段 14](learning/14-memory-and-rollouts.md)。

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
