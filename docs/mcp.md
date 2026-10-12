# MCP：GitHub 与文件管理器

Aster 提供可选 MCP 客户端，通过官方 Python SDK 连接本地 stdio 和远程 Streamable HTTP 服务。工具沿用 Runtime 的参数校验、审批、Hooks、执行记录和观察归档；配置为空时不加载 MCP SDK，也不增加模型工具。

## 启动

项目的 `environment.yml` 与 README 使用名为 `aster` 的 Conda 环境。已有该环境时，先激活，再安装 MCP 可选依赖：

```bash
conda activate aster
python -m pip install -e '.[mcp]'
aster --config mcp.toml.example --cwd /path/to/workspace
```

可用 `conda env list` 查看环境位置，用 `python -c 'import sys; print(sys.executable)'` 确认当前解释器。若使用其他虚拟环境，也应将 MCP 依赖安装到运行 Aster 的同一环境中。

仓库根目录的 [mcp.toml.example](../mcp.toml.example) 可以直接使用，也可以复制配置段到自己的 TOML。它启用 GitHub 官方远程服务和本仓库的文件管理器。`--cwd` 指向已有工作区；文件管理器中的相对路径以该工作区为根。

GitHub 需要在**启动目录**的 `.env` 或进程环境中配置 `GITHUB_TOKEN`。使用 GitHub Personal Access Token，并按实际仓库访问需求授予读取权限；组织策略也可能限制服务访问。不要把 token 写进 TOML、命令参数或对话。配置的只读端点是 `https://api.githubcopilot.com/mcp/readonly`，工具白名单覆盖仓库/源码搜索、读取文件、Issue、PR 和提交查询。未配置 token 时，发现结果会说明缺少的变量，文件服务仍然可用。GitHub 的代码搜索等能力还受账号权限、索引及服务限额影响。

可以输入：

> 用 GitHub MCP 调研 earendil-works/pi 的工具发现与加载机制，引用仓库路径和相关链接。

> 用 filesystem MCP 列出当前目录，创建 notes 目录，写入 notes/hello.txt，读回核对，再删除这个测试文件。

默认 `approval="ask"`，写入会进入 Aster 的既有审批流程。若只需要本地文件服务，可将 GitHub 条目设为 `enabled=false`，并保持 `network=false`。

## 工具怎样进入模型上下文

1. 配置 MCP 后，模型得到 `mcp_list`，其中列出已启用的服务器名称。
2. `mcp_list` 发现工具，并按 `server`、可选 `query`、`offset` 和 `limit` 返回名称与描述。查询使用名称/描述中的关键词匹配；不调用额外 LLM。空查询可分页遍历全部可用工具。
3. 模型使用现有 `tool_load` 加载选中的 `mcp__服务器__工具名`，下一轮拿到完整 JSON Schema 并直接调用。较长或特殊字符的工具名会附加稳定哈希，避免名称冲突和模型接口的长度限制。
4. `refresh=true` 重新发现工具；刷新失败会移除该服务器旧目录，避免继续调用已撤回的工具。恢复会话时，已加载的 MCP 工具会从原配置重新发现。

目录属于单个 Runtime，服务器工具不会进入全局内置工具表。远程参数按服务器的 JSON Schema 校验；同轮有参数错误时，整个工具批次仍会在执行前被拒绝。Schema 只允许本地 `$ref`，不下载远程引用。

MCP 返回值作为不可信的协议 JSON 进入观察记录。超过 8,192 字符的模型预览会截断，完整结果可用 `observation_read` 分页读取。服务端返回的字段不能设置 Aster 的测试通过状态或编辑前读取证据；MCP 读取也不代替内置 `patch_file` 所要求的 `read_file`。

## 配置字段

每个 `[mcp_servers.NAME]` 条目代表一个显式信任的服务。名称以字母开头，只允许字母、数字和下划线，最长 24 字符。

| 字段 | 用途 |
| --- | --- |
| `command` / `args` / `cwd` | stdio 启动命令、独立参数与工作目录；`command="python"` 使用运行 Aster 的解释器；相对 `cwd` 相对于工作区 |
| `url` | HTTP 服务地址；与 `command` 二选一；远程必须 HTTPS，仅回环地址允许 HTTP |
| `env` | stdio 环境变量映射：服务端变量名 → 本地凭证变量名，例如 `{GITHUB_PERSONAL_ACCESS_TOKEN="GITHUB_TOKEN"}` |
| `bearer_token_env` | HTTP Bearer token 的本地环境变量名 |
| `headers_env` | HTTP 请求头 → 本地环境变量名；适用于自定义鉴权头 |
| `enabled` | 是否启用，默认 `true` |
| `enabled_tools` | 可选工具名称白名单，填写服务端原名；省略时发现所有工具，空列表禁用所有工具 |
| `read_only_tools` | 由使用者声明可信只读工具的原名列表；默认空，其余工具均按写操作审批 |
| `timeout` | 一次连接、握手和操作的总超时，默认 30 秒，范围 `(0, 300]` |
| `fallbacks` | 按顺序尝试的备用服务器名称列表，默认空；引用同一配置中的条目，未知名称和循环引用会被拒绝 |

所有凭证引用从 `.env` / 进程环境解析，进程环境优先。凭证值不进入检查点或配置身份；工具结果和诊断沿用脱敏。缺少凭证、启动失败和网络错误会按服务器报告，不使其他服务失效。

HTTP MCP 要求 `network=true`。本地 HTTP 地址不经过环境代理，远程 HTTP 保留环境代理设置。外部写操作受 `read_only`、计划模式和审批策略约束；设置 `write_scope` 时禁用 MCP 写工具，因为通用协议无法证明远端操作符合本地路径范围。服务端自报的 `readOnlyHint` 不授予只读权限。

配置本地 stdio 服务意味着信任其启动程序：它是单独的宿主进程，**不继承 `run_shell` 的 Landlock/seccomp 沙箱**，`network=false` 也不能约束任意第三方 stdio 程序的联网。仅连接可信服务，使用其自身的目录/账号权限限制。文件管理器示例在服务端额外执行下述路径检查。

## 备用服务、熔断与幂等

在主服务上配置 `fallbacks`，指向你信任且语义等价的备用服务。以下域名是占位示例，需要替换为自己的服务；现有 GitHub 示例只配置了官方主端点，没有默认备用地址。

```toml
circuit_failures = 3
circuit_cooldown = 30
network = true

[mcp_servers.primary]
url = "https://primary.example.com/mcp"
bearer_token_env = "PRIMARY_MCP_TOKEN"
fallbacks = ["backup"]
enabled_tools = ["search"]
read_only_tools = ["search"]

[mcp_servers.backup]
url = "https://backup.example.com/mcp"
bearer_token_env = "BACKUP_MCP_TOKEN"
enabled_tools = ["search"]
read_only_tools = ["search"]
```

发现工具和工具调用遇到连接失败、超时、限流或已知服务故障时，会按配置尝试备用服务。每个候选在一次操作中最多尝试一次；模型看到的名称保持为 `mcp__primary__search`，结果记录实际 `mcp_endpoint`。备用服务必须提供同名工具和相同输入 Schema，只读工具还必须在备用服务的 `read_only_tools` 中声明。鉴权、参数及工具业务错误直接返回，不触发切换。

各端点默认连续故障 3 次后冷却 30 秒；冷却期跳过该端点，到期只放行一个恢复探测，成功后自动回到主服务。工具发现结果含 `sources` 和 `health`，可检查当前来源及 `closed` / `open` / `half_open` 状态。熔断状态属于当前 Runtime，进程重启会重置。

**写操作只在尚未发送工具调用时允许切换**，例如启动或握手失败。一旦已经发送写调用，随后超时或断连就视为结果不确定，不会在备用服务重放。正常收到的 `isError` 也不会自动重试。读取没有这类副作用，可在发送后失败时切换。

MCP 写工具的 Schema 会增加可选参数 `_aster_idempotency_key`。模型或调用者应为一次逻辑操作使用稳定键，重试时继续使用该键；Aster 会在发送前提取它，服务器只收到原工具参数。成功或明确返回的结果保存到当前会话的操作记录，相同键的后续调用直接返回旧结果；不同参数复用键会被拒绝，未确定的写入保持阻塞。省略键时以 `call_id` 为键，换一个调用 ID 就是另一次操作。核对远端效果后，只有需要执行新的操作时才换键。这个客户端保护不等同于跨服务的分布式事务；多个服务必须由配置者保证指向同一逻辑资源。

## 自编文件管理器

[mcp_files.py](../src/aster/mcp_files.py) 是独立的 Python MCP 服务，可以供其他客户端使用：

```bash
python -I -m aster.mcp_files --root /path/to/allowed-directory
# 显式启用写工具
python -I -m aster.mcp_files --root /path/to/allowed-directory --allow-write
# 安装后也可使用 aster-mcp-files 入口
```

默认只提供 `list_directory` 和 `read_file`；`--allow-write` 增加 `create_directory`、`write_file`、`delete_file`。目录列表和文本读取均支持分页；文件最多 1 MiB。

示例使用 Python 的 `-I` 隔离模式，从已安装的 Aster 包启动，避免工作区中的同名 Python 包覆盖服务代码。

路径复用 Aster 的工作区检查：拒绝越界、符号链接、特殊文件、`.env` / 密钥文件和 `.git` / `.aster` 等控制目录。文件读取还拒绝硬链接；实际读写使用目录文件描述符和 `O_NOFOLLOW`。写入省略 `expected_sha256` 时只允许新建，替换或删除必须提供此前读取的文件哈希；替换使用临时文件和原子重命名。外部并发编辑仍需调用者协调，不提供跨进程文件事务。目录创建只处理一级，删除只处理文件，没有递归删除。

官方经典实现是 [`@modelcontextprotocol/server-filesystem`](https://github.com/modelcontextprotocol/servers/tree/main/src/filesystem)。也可以通过 `command` / `args` 接入它，显式传入允许目录，并自行配置只读工具名。本仓库的 Python 示例方便阅读、测试和独立运行，工具范围比官方实现小。

## 首版边界与验证

每次目录发现或工具调用均建立并关闭一条 SDK 会话，生命周期与超时、取消在同一个异步任务内完成。工具定义在 Runtime 中缓存，连接不跨调用复用。因此当前适用于 GitHub、文件操作等不依赖会话内临时句柄的工具；需要保留会话状态的服务应等后续连接复用实现。stdio 每次启动会有额外耗时。

当前实现 Tools 能力与文本/结构化结果记录；未接入 OAuth 登录、Resources、Prompts、Sampling、交互式 Elicitation 或图片/音频的模型呈现。恢复会话不重放未确认的写操作，超时/取消后的实际副作用需要核对。主备切换和熔断不会改变权限、模型请求的原有退避策略或会话预算。

```bash
python -m pip install -e '.[dev,mcp]'
python -m pytest -q tests/test_mcp.py
```

测试启动真实 stdio 子进程和带鉴权的本地 HTTP MCP 服务，覆盖发现、分页、加载、参数错误批次、文件操作、权限、恢复、凭证脱敏、长输出归档、超时和取消。它们不使用 GitHub 凭证，也不调用模型 API；GitHub 账号权限与真实查询应在配置 token 后单独验证。

2026-10-12 本地验证（Python 3.12，安装构建后的 wheel）：核心回归 **447 项通过**，其中 MCP 测试 12 项；CI 范围的 Ruff 检查、依赖一致性及 `git diff --check` 通过。其他回归有 6 项小上下文预算测试失败，已在修改前的 `c6b651d` 源码上复现，涉及 `test_context_archives.py` 与 `test_context_policy_input.py`；两项离线打包测试补齐验证环境依赖后通过。当次验证未配置 GitHub token，未验证带凭证的 GitHub 查询。

2026-10-13 在已有的 `aster` Conda 环境（`/home/dzh/.conda/envs/aster`，Python 3.12.14）中安装 MCP 依赖，12 项 MCP 测试及依赖一致性检查通过。使用本地配置的 token，通过 Aster Runtime 成功发现 9 个 GitHub 只读工具，按需加载工具，搜索 `earendil-works/pi` 并读取其 `README.md`；结果进入观察归档。此次真实连接验证未调用模型 API。

2026-10-13 补充备用服务、熔断、幂等与运行中追问后：核心回归 **464 项通过**，其他回归 **788 项通过、4 项跳过**；失败仍为前述基线中已复现的 6 项小上下文预算测试。CI 范围的 Ruff、依赖一致性和 diff 检查通过；GitHub 真实工具发现及仓库搜索再次通过。新增测试覆盖真实 HTTP 主备切换与主服务恢复、备用 Schema/权限约束、未知写入禁止重放、跨恢复的操作结果复用、熔断单探测与取消、多会话消息归属，以及模型/工具/审批期间收到追问。

提交前仅应用本次功能改动的隔离验证：核心回归 **468 项通过**，其余回归 **787 项通过、4 项跳过**，失败仍为相同的 6 项基线上下文预算测试；此范围不包含工作区中另外的教学网站改动。构建并安装 wheel 后，20 项 MCP 测试、三个 CLI 的帮助入口及依赖一致性检查通过。新增回归还覆盖文件服务启动时的工作区包隔离、子会话继承环境文件凭证、外部策略接收追问，以及同批刷新目录后的工具撤回和 Schema 变更。
