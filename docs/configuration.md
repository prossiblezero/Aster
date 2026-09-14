# 配置与字段说明

优先级：显式 CLI 参数 > `ASTER_*` 环境变量 > TOML 文件 > Python 默认值。

| 字段 | 含义 |
|---|---|
| `protocol` | `responses`、`chat` 或 `anthropic` 请求格式 |
| `model` | provider 模型名；当前评测用 `gpt-5.6-luna` |
| `base_url` | provider 根地址，不包含 `/responses` 等路径 |
| `api_key` | API key；只从本地环境读取，不提交 |
| `approval` | `ask` 询问、`auto` 自动允许策略内动作、`never` 无人时拒绝需审批动作 |
| `sandbox` | `required` 不可用即拒绝，`best_effort` 可记录降级，`off` 不隔离 |
| `network` | 是否给隔离子进程网络；默认 false |
| `context_tokens` | 输入预算，需大于输出预留 |
| `tool_output_tokens` | 单条工具观察在送入模型前的 token 上限 |
| `tool_profile` | `core` 只展示常用工具；`all` 展示全部工具 |
| `auto_dream` | 运行结束后是否执行一次确定性记忆去重 |
| `write_scope` | 相对工作区的允许写入前缀 |
| `completion` | `strict` 失败即停止，`warn` 允许带警告完成 |

训练/CI 推荐 `sandbox=required, approval=never, network=false`。
