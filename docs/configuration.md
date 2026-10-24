# 配置与字段说明

激活 `aster` 环境并配置好模型后，在交互终端中直接运行 `aster` 即可进入 TUI；`aster '任务内容'` 执行一次任务后退出。工作区默认为当前目录，也可用 `aster --cwd /path/to/project` 指定。`aster --tui`、`aster-tui` 仍可显式启动 TUI，任务需在界面中输入，不能同时传入命令行任务文本。`--resume`、`--rl-smoke` 保留各自的执行方式。非交互输入下裸启动会报错，避免等待无法提供的输入。

大多数命令行参数是可选覆盖项。CLI 自动读取**启动命令所在目录**的 `.env`，并非 `--cwd` 指向的任务仓库；需要其他配置文件时，用 `--env-file /path/to/.env` 指定模型连接配置，或用 `--config aster.toml` 读取 TOML。

配置优先级是：显式 CLI 参数 > 进程环境变量 > `.env` > TOML > [Settings 默认值](../src/aster/config.py)。当前只读取四个 `ASTER_*` 环境变量：`ASTER_PROTOCOL`、`ASTER_MODEL`、`ASTER_BASE_URL`、`ASTER_API_KEY`。其余设置通过 TOML 或 Python 的 `Settings(...)` 传入；没有 `ASTER_MAX_WORKERS` 之类的隐含开关。

具名模型可放在同一 TOML 的 `[profiles.alias]` 中。`--profile alias` 或 TUI 空闲时 `/model alias` 同时选择服务、协议、凭证和预算；`/model` 列表不进入模型提示。显式 profile 不被旧 `ASTER_MODEL/BASE_URL/PROTOCOL/API_KEY` 元组覆盖，其 `api_key_env` 从 `.env`/进程环境解析（进程优先）；明确 CLI 模型/协议/预算覆盖最后生效。当前支持空闲同会话切换，先校验和转换历史及预算；正在执行模型/工具/Worker 时拒绝切换。下文列出历史转换与真实服务验证的边界。

```toml
[profiles.remote]
provider = "gateway"
model = "your-model-id"
protocol = "responses"
base_url = "https://your-provider.example/v1"
auth = "required"
api_key_env = "MY_MODEL_KEY"
context_tokens = 128000
output_tokens = 32768
stream = true

[profiles.local]
provider = "local"
model = "local-model-id"
protocol = "chat"
base_url = "http://127.0.0.1:8000/v1"
auth = "none"
context_tokens = 24000
output_tokens = 2048
```

profile 不接受权限字段或明文凭证，切换不改审批、沙箱、联网及写入范围。缺少认证、非法预算或未知名称时原配置保持不变；不写回配置文件。本地服务可显式 `auth="none"`，请求不带认证头。

交互入口在未显式配置时默认启用 Responses 流式；单次 CLI 任务和直接使用 `Settings()` 默认非流式。`stream=true` 或 `--stream` 可启用流式，显式 `stream=false` 或 `--no-stream` 可关闭。Chat/Anthropic 默认非流式，强行启用当前不支持的流式会明确拒绝。TUI 提前显示文字片段及接口实际返回的思考摘要，并标注请求尝试；重试不拼接旧文字，取消/断流保留未完成提示，所有工具仍等完整成功终态及参数校验后执行。每次尝试的进度展示最多 512 条增量事件及 1,048,576 字符，达到此上限只裁剪展示并标记 `progress_truncated`，完整终态仍可成功。单 SSE 事件 16 MiB、整流 64 MiB 是传输硬上限，超过后拒绝响应并不执行该轮工具。首事件、首有效文字和完成时间分别记录；缓存/推理参数没有随开关改变。

## MCP 外部工具

安装 `python -m pip install -e '.[mcp]'`，通过 TOML 的 `[mcp_servers.NAME]` 配置本地 stdio 或远程 Streamable HTTP 服务。示例为 [mcp.toml.example](../mcp.toml.example)，字段、凭证及权限说明见 [MCP 指南](mcp.md)。

每个 Runtime 独立发现工具，`mcp_list` 返回目录，`tool_load` 加载完整 Schema。HTTP 服务要求 `network=true`；写工具沿用审批、只读与计划模式限制。`circuit_failures=3`、`circuit_cooldown=30` 控制 MCP 端点的故障冷却，`fallbacks` 指定语义等价的备用服务。凭证从 `.env` 或进程环境解析，配置保存变量名称。

## 模型与输入预算

| 字段 | 默认值 | 含义与边界 |
|---|---|---|
| `protocol` | `responses` | 请求格式；另有 `chat`、`anthropic`。真实模型评测使用 Responses；后两者验证了请求与响应契约，尚不代表真实服务验证 |
| `model` | `gpt-5.6-luna` | 网关识别的模型名称 |
| `base_url` | `https://api.openai.com/v1` | API 根地址，不附加 `/responses` 等具体接口路径 |
| `api_key` | 空 | 连接凭证；建议放在未提交的 `.env` 或进程环境，禁止写入已提交配置 |
| `request_timeout` | `90` | 大于0、至多300秒。HTTPX连接/读取等阶段的超时；流式还以同值限制每次尝试的总时长。瞬时错误最多尝试三次，因此整个模型轮次可能超过此值 |
| `context_tokens` | 不设置（`None`） | 按已知模型规格计算；显式填写时仍表示输入与输出预留之和，可用于网关限制或压力实验 |
| `output_tokens` | 不设置（`None`） | 按模型和总预算选取输出预留，同时作为供应商单次请求的最大输出长度；显式值优先 |
| `tool_profile` | `core` | 首轮展示可用核心工具，其他工具由 `tool_load` 按需加入；`all` 展示当前配置可用的全部工具 |

默认预算来自已核对的模型配置，而不是接口协议。按 [OpenAI 官方 Luna 规格](https://developers.openai.com/api/docs/models/gpt-5.6-luna)，总窗口为 1,050,000、最大输入为 922,000、最大输出为 128,000 tokens。这三个数字不能互换。自动输入额度为 `floor(min(最大输入, 总窗口 - 有效输出预留) × 0.95)`，留出 5% 本地计量余量；Luna 默认得到 **875,900 输入 + 32,768 输出预留**。这是容量上限，不会为了填满窗口而构造无关输入。

输出预算的选择顺序是：显式 `output_tokens`；否则显式 `context_tokens` 时，取总预算的四分之一与 32,768、已知模型最大输出三者的最小值；否则已知模型取 32,768 与模型最大输出的较小值。未知模型且未显式给总预算时保留 2,048 的保守回退，CLI 会标明并提示覆盖。这个回退不代表长代码生成的推荐值。输出预算包含供应商计入的推理 token 和工具调用参数，并不只限制最终给用户的文字；到达输出上限的响应按 `incomplete_response` 停止，未完成的工具参数不会执行。

流式总时限不会因为收到增量而重置，和等待下一块网络数据的读取空闲超时不同。长代码响应即使没有达到 `output_tokens`，也可能被这个时限中断；按实际服务延迟可显式调大 `request_timeout`（最高300），但这不保证服务成功。非流式当前仅使用HTTPX阶段超时，尚未统一两种模式的总截止语义。

CLI 可直接覆盖预算及每次任务轮数，不必编辑 TOML。例如 `aster --context-tokens 128000 --output-tokens 32768 --max-steps 80 --max-turns 60 '完成任务'` 使用 95,232 输入预算与 32,768 输出预留。`--max-steps`、`--max-turns` 同时适用于单次 CLI 任务和 TUI 内的各次任务，取值范围为 1–500，默认均为 40；例如 `aster --max-steps 80 --max-turns 60` 会以这些任务上限启动 TUI。这两个参数与 `--rl-smoke` 同时使用会明确拒绝。

当前内置规格只有精确名称 `gpt-5.6-luna`；不是从任意兼容接口自动发现窗口。未知名称回退到总额 24,000，并在 CLI / TUI 明示。接入网关若有更小限制，可显式配置 `context_tokens`；已知模型超过官方输入、输出或总窗口上限会报错。显式值不会再次扣除 5%，便于准确复现实验。恢复检查绑定最终生效额度，而不是 `None`。

已移除 `tool_output_tokens`：工具正文不再预先截到固定 token 数，整份请求超预算后才压缩。旧 TOML 中该字段需删除，否则明确报配置错误。执行超时、进程输出采集大小、读取行数和分页字符数仍各管自己的资源边界；它们不等于模型上下文预算。

例如 `context_tokens=4096`、`output_tokens=512`，实际输入上限就是 `3584`。[ContextBuilder](../src/aster/context/builder.py) 使用与 Provider 相同的 [request_payload](../src/aster/providers/codec.py) 请求构造函数，把工具名称、说明、参数 schema、消息和协议包装一并计数。无法保留当前任务及必需交互时，返回 `context_budget_exceeded`，不继续发送已知超预算请求。

这里的 token 来自本地 tokenizer，对远端模型仍是估算。图片编码、供应商内部模板和计费规则可能不同；费用与真实 usage 以网关返回结果为准。详见 [上下文预算](learning/06-context-budget.md)。

运行 `usage` 保留供应商实际报告的输入、输出、总量，以及缓存读取 `cache_read_input_tokens`、缓存创建 `cache_creation_input_tokens`、推理 `reasoning_tokens`。未报告或非法的数值缺省，表示 **unknown**；供应商明确返回的 `0` 才表示已知为零。每次收到可解析模型响应记录 `usage_requests=1`，每个已知指标同时记录 `<指标名>_requests=1`；Runtime 累加这些计数，因此两次响应只有一次报告缓存时，缓存数值是已知部分之和，覆盖率为 `cache_read_input_tokens_requests / usage_requests = 1/2`，不能作为完整缓存总量。没有收到响应的传输失败不表示免费调用，账单仍须核对供应商。

Responses/Chat 的缓存读取与推理量分别属于已报告输入、输出的子集，不再加到总量。Anthropic 的原始 `input_tokens` 只含未缓存输入，保存为 `uncached_input_tokens`；它与缓存读取、缓存创建三项都报告时，才生成可比较的完整 `input_tokens`。总量优先采用供应商报告，未报告时仅在完整输入与输出均已知时相加。未知缓存或推理量不能按零计算成本，也不能据此宣称缓存命中率或记忆节省。

`todo_list` 与 `skill_load` 使用字符分页：`offset` 从 `0` 开始，`limit` 默认 `512`、范围 `1–1000`；结果正文第一行给出 `next_offset` 和 `total`。全局压缩需要缩短该结果时会自动缩小页面，因此后续调用必须使用返回的 `next_offset`，不能直接加上请求的 `limit`。`next_offset=null` 表示到达当前文本末尾；`total` 是脱敏后全文的字符数。先脱敏整份来源再分页，因此密钥替换不会改变页间定位，也不会因跨页拆开密钥而漏掉脱敏。`skill_load` 还需保留技能 `name`；目录包资源使用 `resource` 相对路径显式读取，详情见 [Skills 包与读取边界](skills.md)。

`observation_read` 回读当前运行及续聊时登记的同会话祖先归档：带 `observation_id` 时使用上述字符分页，不带编号时按记录分页（单次最多 10 条）；`query` 是可选字面检索。始终进入可加载目录，通过 `tool_load` 按需加载；尚无归档时返回空列表。恢复时校验祖先会话、安全身份与归档完整性，不开放任意运行或状态文件。实现与限制见[上下文归档](learning/07-context-archives.md)。普通运行沿用默认预算自动检查，无须手工选择评测中的三档配置。

`list_files` 与 `search` 按记录分页，`offset` 从 0 开始，`limit` 默认及最大为 500；搜索仍是忽略大小写的字面匹配。模型能看到 `count`（本页条数）、`truncated`、`next_offset`；文件列表另有精确 `total`。搜索只读取足以确定下一页的命中，不伪造全部命中总数。若上下文预算随后压缩某页正文，这些字段描述原始工具页，可通过归档读取其完整内容；不要把摘要中的行数当成实际返回条数。

普通工具的完整结果保留在 trace；分页工具返回当前页，完整计划及已选技能正文快照保存在运行状态中。技能正文默认沿用首次成功加载时的脱敏版本，只有 `refresh=true` 且页面成功时才更新；工具结果及动态上下文给出版本号，版本改变后应从 `offset=0` 重读。资源仍实时分页读取，不支持 refresh。自动摘要被截断时可以继续翻页，选中技能不等于看完全文。正文与元数据的 JSON 结构、中文分页示例见 [归档与分页](learning/07-context-archives.md)。

Shell/测试输出最多采集 4 MiB，正常、失败及超时结束均在脱敏后归档；模型正文只保留最多 8,192 字符预览，用结果里的 `observation.id` 经 `observation_read` 回读。`output_bytes/captured_bytes/output_truncated` 明确说明资源超限；超过采集上限的部分无法回读。取消中的输出暂不保留。归档失败会记录已执行命令及已修改文件，然后以 `output_archive_unavailable` 停止。

`patch_file` 可传 `edits=[{old_text,new_text}, ...]`（1–100 项），每项都匹配同一原文的唯一、不重叠区域；任一无效则整批不写入。旧 `old_text/new_text` 仍支持，但不能同时传两种形式。补丁保留 BOM 和未改区域换行；每次成功后仍需重新读取才能继续编辑。

`run_tests.code` 可选传入临时 Python 测试源码（最多 65,536 字符），配合 pytest 或非 discover 的 `python -m unittest`，与真实项目一起在已有隔离副本执行，不写回项目。已有测试继续只传 `command`。临时源码仍需定义真实框架用例；打印成功不能代替验证。该能力不需要新增配置，长期回归测试应保存在项目中。

## 权限与执行范围

| 字段 | 默认值 | 含义与边界 |
|---|---|---|
| `approval` | `ask` | `ask` 需要真实用户批准；没有回调就拒绝。`auto` 允许策略内的常规写入或执行；显式 `ask` 规则仍会询问。`never` 不提供人工批准，拒绝仍需审批的动作 |
| `sandbox` | `required` | `required` 要求隔离可用；`best_effort` 仅在没有只读/写入范围约束时可记录降级并直接运行；`off` 关闭隔离，也不能执行需要隔离保证的只读或范围约束命令 |
| `network` | `false` | 隔离子进程是否可以联网；不控制 Harness 自身调用模型 API 的网络 |
| `read_only` | `false` | 阻止写工具；Shell 在隔离副本内运行且不能把修改带回工作区 |
| `write_scope` | `None` | 工作区内允许写入的相对路径前缀。`None` 不额外收窄；`[]` 不允许写任何文件；`["src", "tests"]` 仅允许这两个路径及其后代 |
| `rules` | `[]` | 命令参数前缀规则；字段解释见下文 |
| `max_workers` | `0` | 模型自主派工的并发上限；0 时面板仍可显式启动最多两个只读子任务。已结束不占名额，子任务不能递归派工 |
| `read_parallelism` | `1` | 同轮全部为 `read_file/search/list_files` 时的并发上限，范围 1–8；混有其他工具的整批串行，与 Worker 开关独立 |
| `reuse_environment` | `false` | 仅 required 沙箱可开启；同一 Runtime 跨命令保留临时 `.venv`/`node_modules`，不回写宿主依赖，新会话/进程需重建。详见[执行环境](execution-environment.md) |

`approval=auto` 无法覆盖工作区越界、只读、禁止规则或 Hooks 拒绝。TUI 的 **仅允许本次** 只批准当前动作，不修改配置。`plan_enter` 进入计划模式后，写工具被拒绝，Shell 只能在副本中观察；`plan_exit` 仍经过权限判断。

工具目录与执行入口使用同一份能力判断：关闭持久记忆、禁用 Worker、无法询问用户、只读/计划模式或关闭所需沙箱时，不展示对应不可用工具。`tool_load` 的名称目录也同步收窄；直接提交动作仍会再次校验。文件编辑前仍要求最新读取证据，这属于动作前置条件，不会永久隐藏写工具。

文件写入将审批前的内容版本（新文件则为“尚不存在”）传递到执行器，在执行前和建立隔离副本后重新核对。等待审批、前置 Hook 或复制期间发生变化时返回 `fresh_read_required`，要求重新读取；审批不能授权覆盖随后出现的用户修改。发布仍检查执行期间的并发变化，跨独立进程的最终检查与替换并不构成文件系统事务。

`max_workers=0` 关闭模型的 `worker_start`。已有用户显式创建的子会话时，父模型可以加载 `worker_status/message/stop` 整合或补问；没有子会话时整组隐藏。子任务自己的目录仍关闭整组工具；`loaded_tools` 不能绕过能力检查。`all` 表示当前配置下可用的全部工具，不表示忽略功能开关。

子会话恢复遵循父分支保存的不可变历史快照。重启只查看结果，明确续问才恢复执行并继续消耗原团队余额；旧父分支不能看到子任务的未来消息或观察。快照缺失会显示不可用，不能自动采用最新子状态；冷恢复时保留结果和读取证据，不展示会持续增长的最近工具轨迹。

默认 `core` 模式按需提供完整工具定义：首轮最多包含 9 个核心工具，`tool_load` 的参数列出可选工具名称；调用 `tool_load(names=["todo_add"])` 后，下一轮才加入 `todo_add` 的说明和参数结构，其他可选工具不会一起加入。这是模型工具定义的逐步展开，不是 Python 模块的延迟导入，也不是权限边界；宿主直接调用已实现的工具仍由共享权限和可用性检查裁决。`all` 用于需要一次展示全部可用定义的场景和对照评测，本身不按需展开。

关闭 `memory_evolution` 或 `auto_dream` 仍保留存储、召回、更新和删除工具：它们分别控制反馈维护和自动整理，没有关闭长期记忆本身。关闭 `network` 也保留 Shell，用于本地操作。配置矩阵及复现方法见 [数据集说明](../datasets/README.md#工具可见性配置矩阵)。

没有审批回调时，只展示能由现有允许规则授权的执行工具，并继续尊重更高优先级的拒绝。例如只允许 `echo` 时可展示 `run_shell`，不会因此展示 `run_tests`；允许 `python -m pytest` 才能授权对应测试命令。之后接入或移除审批回调，目录随之更新。

沙箱复制任务文件，排除 `.git` 等控制元数据。因此即使原仓库有 Git，副本中也不能依赖 `git status`；`git_diff` 展示 Aster 保存的初始文本及权限模式与当前文件的差异。禁网保留匿名本地 IPC，支持 asyncio 与线程唤醒；仍拒绝外部网络和命名 Unix socket。文件回写逐文件原子，批次失败保留并报告已经写入的路径；不覆盖外部编辑做回滚。当前支持同一进程内的 Worker，并不承诺多个 Aster 进程同时写同仓库的事务隔离。

Git 工作区通过本机 Git 排除未跟踪且被忽略的文件与目录，遵循嵌套 `.gitignore`、否定规则及 Git 标准排除配置；已跟踪文件不会仅因忽略规则被排除。工作区扫描、快照和沙箱输入复制使用相同范围，仍保留 10,000 文件上限；敏感文件、符号链接和控制元数据限制始终优先。忽略文件可以按明确路径读取，但不能写入或从沙箱回写；命令若生成此类输出，整批回写会以 `git_ignored_path` 拒绝，应把临时输出放入沙箱的 `.tmp/`。非 Git 目录（含不带 `.git` 的干净副本）或未安装 Git 时沿用内建排除规则，不解析 `.gitignore`。Git 检查在已识别的仓库中失败时明确拒绝，不静默扩大范围。

显式选中已被父 Git 忽略的目录作为工作区时，以所选根作为忽略边界，不继承祖先 `.gitignore`；仍保留目录内、全局与 info 排除，以及父仓库已跟踪路径。这一启动方式不创建 `.git`、不修改父仓库 index，也不放宽敏感文件、符号链接或写入限制。

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
| `memory_evolution` | `true` | 开关经验提炼、使用反馈、基于反馈的排序及 Dream；关闭后仍可保存知识，来源失效和安全准入仍生效 |
| `auto_dream` | `true` | 任务结束登记待整理材料；规则停用失效条目，后台沿用当前模型有界提炼和合并 |

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

Runtime 只对未显式设置的条件做文字推断；显式 `False` 会保留。正式评测应在任务数据中明确这些条件。`require_edit` 表示本轮发生过修改，目标代码是否正确仍由独立验证器判断。内容或权限位变化会使旧验证失效；checkpoint 指纹现在包含文件权限位，因此旧版本的指纹可能被拒绝恢复，不能将其当作当前有效状态。用于 RL 奖励的独立验证器另行接入，不能用 `completion="off"` 关闭它，见 [环境接口](learning/15-rl-interface.md)。

同一 Runtime 同时只接受一个运行操作，单个 RL episode 也只允许一个在途 `step`；并发调用明确返回 busy。idle 状态重新 `start` 会以 `superseded` 结束旧任务，`resume` 不会覆盖活动任务。终态自动汇总当前任务 Worker 的修改，旧任务的 Worker ID 不能污染新任务；取消后迟到的审批或模型返回不能继续写文件。

TUI 普通追问延续同一会话，`/new` 开新会话；`/continue` 从中断状态继续。每次续聊分配新的 `run_id` 和有界步数/轮数，保留 `session_id`、原请求、历史和已执行调用 ID。未完成任务的反馈继承旧交付要求，不能用“继续”绕过修改、测试或必需产物。已完成任务的新追问按新反馈推断要求；新反馈会使旧测试及读取证据失效。显式 `Runtime.continue_task(max_steps=..., max_turns=...)` 可设置本次预算。

CLI `aster --resume RUN_ID [反馈]` 或 TUI `/resume RUN_ID` 恢复后启动上述续聊。默认拒绝模型身份或工作区漂移；明确加 `--allow-model-change` 才转换模型历史，加 `--adopt-workspace` 才接受外部修改并清除旧读取/测试证据。TUI `/continue --adopt-workspace` 同样支持工作区采纳。模型变化不允许安全身份变化。

空闲时 `/model <alias>` 现在作用于同一会话：先校验完整历史、工具配对和新预算，再保存原生历史证据及切换事件。跨 Responses、Chat、Anthropic 转换保留文本与工具结果，去除不兼容的不透明推理/签名并记录数量；普通图片、引用等尚不支持的语义内容会明确拒绝，不能当成无损转换。新旧凭证共同脱敏，无法安全保存的不透明内容导致切换失败。配置、历史或预算预检失败保留原会话。较小窗口可能压缩历史，旧观察仍需从已授权归档回读。

实际发送内容与请求预算共用脱敏后的消息；切换或恢复（包括只轮换凭证、模型身份不变）会重新过滤可见历史和 Skill 快照。不透明内容仍原值校验。若凭证与已选技能别名、模型/工具定义或必须保留的协议控制字段冲突，则拒绝操作，不能通过重命名、删除技能或改写 phase 来绕过。

只读批次顺序完成参数、权限、Hook 检查及步数预留，再在有界线程中执行；结果按调用顺序更新历史、读取证据和工作记忆。失败保留其他结果，取消等待已开始的线程退出且不发布迟到结果。写入、Shell、测试、审批交互及状态修改未启用并行。

一个只读浏览配置可以写成：

```toml
# browse.toml
approval = "never"
sandbox = "required"
read_only = true
network = false
# 不填写 context_tokens，使用接入模型的默认预算
output_tokens = 2048
tool_profile = "core"
```

```bash
# 先按学习第 00 章创建演示目录
aster --config browse.toml --cwd /tmp/aster-learning-demo '查看仓库并说明入口'
```

检查代码中的实际行为：

```bash
python -m pytest -q tests/test_new_contracts.py tests/test_safety.py tests/test_interaction_workers.py
```

## 内建测试的语言范围

`run_tests` 当前接受 `pytest`、`python -m pytest`、`python -m unittest` 和 `node --test`，使用结构化结果形成验证证据。Node 使用内置 JUnit reporter，拒绝无显式测试、全跳过及报告缺失；npm 脚本仍通过 `run_shell` 执行，不会仅靠退出零成为测试通过证明。授权 DNS 的系统读取及 Node 边界见[执行环境](execution-environment.md)。

## 项目知识 Memory

项目记忆、自进化和任务结束后的自动整理默认开启，CLI 与 TUI 开箱即用，无需用户额外配置。主 Agent 可经 `tool_load → memory_store` 保存长期项目约定、用户纠正和可复用的方法。即使前台没有主动调用保存工具，也会自动提炼已结束任务中的有价值信息；后台沿用当前模型与账户，不增加独立模型服务或配置步骤。

| `kind` | 内容与来源 |
|---|---|
| `excerpt`（默认） | 成功 `read_file` 结果中的短原文，继续核对原文与文件哈希 |
| `fact` | 事实、偏好、约束；可从文件、真实用户请求或执行结果归纳，必须填写适用条件 `when` |
| `procedure` | 可复用方法；来源规则与 `fact` 相同，包括 `run_tests` / `run_shell` 的成功或有退出码的执行错误，必须填写 `when` |

每条最多 1,000 字符，`when` 最多 200 字符。`evidence_id` 引用本次运行的证据；派生知识可使用 `"user"` 引用最近一次真实用户请求，运行时会转换为对应事件的哈希。`evidence_ids` 可补充最多四个来源，组合观察时每个来源都须通过认证。归纳不要求逐字照抄，但来源真实不等于推断正确；记忆始终作为待核对的知识，不授予操作权限。敏感、暂态和越权内容仍拒绝入库。

`memory_update` 按相同规则更新，替代条目入库成功后才停用旧条目。同一知识再次入库时保留已有引用，补充文件证据不会抹去原有用户来源。文件来源的所有哈希都需保持有效；用户约定和执行经验则依靠明确更新、丢弃和使用反馈维护，不会自动理解所有语义冲突。

自动召回结合初始请求和最近用户反馈，按正文、标签及适用条件匹配，最多注入三条并显示 ID。中文使用相邻双字片段；这是词面检索，不是向量语义搜索。保存仍使用本地 JSON，最多 64 条。

三个开关都开启时，`MemoryMaintenance` 在任务结束登记来源，后台一次处理最多三个已结束任务，提供最多六条相关旧记忆，提议至多三项新增、替换/合并、停用操作。修复方法需要真实失败与后续测试通过的引用；普通请求、临时事项和已覆盖内容允许不保存。后台可与新任务并行，前台在请求/工具批次/任务边界提交；内容版本变化会让旧批次作废。模型决定语义，程序检查来源、时间、文件哈希和内容准入。

每批请求的本地输入估算不超过 4,096 tokens，输出上限设为 768；只发一次，不自动重试。供应商实际 usage 可能与本地估算不同，以报告为准。后台调用和已知消耗计入 `team_usage` 与总 `usage`，`memory_usage` 另列其中的维护部分；不要重复相加。未返回 usage 的请求保留预算估计并标记未知。请求尚未启动时发生存储失败，会释放本次预算预留，材料留待后续处理。预算不足就延期，前台优先保留收尾空间。

CLI 先输出任务答案，再给维护最多 15 秒；TUI 可继续交互，退出时取消整理。待处理引用和已生成候选保存在 `.aster/memory/pending`、`jobs`，下次使用可继续处理。成功落盘后显示实际记忆内容；`/memory` 显示有效/停用状态、来源、最近整理和用量。模型常见的“已记住”表述若没有本次写入事件，会追加真实保存状态；这只是常见措辞的补救，不是通用自然语言承诺验证器。

关闭自动整理或后台模型不可用时，保留原有的修复后一次提醒作为回退；没有失败或没有验证通过，不触发修复提醒。完整调研、边界与验收见[相关设计沿革](optimization-history.md)。

启用 `memory_evolution` 时，Agent 应在 `run_tests.memory_ids` 中列出此次实际应用并检验的已召回条目。只有这些 ID 接收明确通过或 `tests_failed` 的反馈；未列出的条目、伪造 ID、环境或收集错误不受奖惩。反馈仍是模型选择条目加测试结果的代理信号，不证明因果关系。确定性的 `Memory.dream()` 继续停用失效或反复失败的记忆，与新增后台提炼各司其职。调研与设计取舍见[相关设计沿革](optimization-history.md)。

## v0.2 默认迁移与恢复

当前新会话默认开启项目记忆、自进化与自动整理，并使用单 Agent（`max_workers=0`）。v0.2 初期曾默认关闭记忆，2026-10-05 调整为开箱即用。普通使用不需要填写这些开关；做实验或需要关闭记忆时，可在 TOML 显式覆盖：

```toml
memory_enabled = false
memory_evolution = false
auto_dream = false
```

恢复旧 checkpoint 必须提供与保存时相同的安全及功能配置；不匹配会列出字段并拒绝，不静默迁移。`--allow-model-change` 只允许模型相关变化，不能越过功能和权限边界。旧 checkpoint 不会因拒绝恢复而重写。v0.2 保存的 checkpoint 使用版本 2，旧版状态可读；新状态须用 v0.2 或更新版本恢复，不能用不识别在途执行标记的旧版恢复。

执行批次开始前保存 `inflight.json`，完整配对的结果原子保存后移除。进程崩溃留下结果不明的调用时，恢复报 `interrupted_tool_batch`；先检查工作区、事件与外部副作用，再从确认过的状态启动新任务。`--adopt-workspace` 不绕过这一限制。完成 checkpoint 已包含整批结果而仅剩旧标记时可以恢复，已完成调用 ID 仍禁止重复。普通 Ctrl+C 会等待清理并保存状态；这不是任意外部命令的 exactly-once 保证。

CLI `--stream` 将按尝试分隔的实时接收进度和正文预览写到 stderr，stdout 只输出最终完整答案。失败尝试的预览标为放弃，不作为已提交答复；为保护被流式分片拆开的凭证，正文按完整行脱敏后显示。CLI 和 TUI 都接受 `--max-steps`、`--max-turns`，停止时显示 run ID、原因、改动数量、验证状态和恢复入口。

Provider 按空回复、不支持的输出、截断、拒绝及坏结构分类。单次逻辑请求最多三次传输尝试，空回复最多重试一次并计入这个总上限；不自动换模型、协议或服务地址。每次尝试的已知 usage 与缺失情况单列，响应模型标签只是服务端声明，不代表已核实实际底层模型。


历史实验的设计取舍与证据边界见[优化历程](optimization-history.md)；已公开的报告与原始数据见[指标证据](evaluation-results.md)。
