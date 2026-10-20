# 02｜把不同模型接到同一个循环

上一章：[Agent 循环](01-agent-loop.md) · [目录](README.md) · 下一章：[工具系统](03-tool-system.md)

> 统一运行时动作，保留协议里不能随意改写的历史。

## 为什么不能只换一个 URL

同样是“读取文件”，Responses API、Chat Completions 和 Anthropic Messages 的 JSON 形状不同。有的把调用写成独立条目，有的放在助手消息的 `tool_calls` 里，有的放在 `content` 的 `tool_use` 块里。

如果循环内部到处判断“当前是哪家模型”，工具、安全、记忆都会被接口格式牵着走。传输与请求记账分别位于 `providers/client.py`、`providers/streaming.py` 和 `providers/usage.py`；Aster 把纯协议适配收在 [providers/codec.py](../../src/aster/providers/codec.py)：请求前转换协议，返回后解析成统一 `ModelReply`，Runtime 只处理 `ToolCall`。

```text
Runtime.prepare_request()
        ↓ 消息 + 工具定义（工具结果已渲染成模型要读的文本）
request_payload() ── 按 protocol 序列化
        ↓
ModelClient._post() ── HTTPX 发送请求
        ↓
协议解析函数 ── ModelReply(text, calls, usage, raw)
        ↓
Runtime 执行 calls，保存 raw，把完整的工具结果记录追加进历史
```

HTTP 传输使用 HTTPX，协议适配由项目自己的 codec 完成。

## 对照一次调用与结果

假设模型提出 `read_file(path="hello.txt")`，编号为 `read-1`。三种协议的对应位置如下：

| 协议 | 工具调用 | 对应结果 |
|---|---|---|
| `responses` | `type=function_call`，编号在 `call_id` | `function_call_output`，同一个 `call_id` |
| `chat` | 助手消息的 `tool_calls`，编号在 `id` | `role=tool`，编号在 `tool_call_id` |
| `anthropic` | 助手 `content` 内的 `tool_use`，编号在 `id` | 用户 `content` 内的 `tool_result`，编号在 `tool_use_id` |

这些编号建立调用与结果的引用关系。丢失调用或重复编号都会破坏这组关系。`history_groups()` 负责验证这种结构，并把完整交互分组，供上下文压缩使用。

这张表是线上格式；Runtime 保存的历史和模型读到的内容是另外两种样子，见下文“同一条工具结果的三种样子”。

## 读懂 ModelReply 的六个字段

| 字段 | 作用 | 为什么需要 |
|---|---|---|
| `text` | 可展示的回复文字 | 用于解释或最终回答 |
| `calls` | 标准化的 `ToolCall` 列表 | 工具执行不依赖供应商格式 |
| `usage` | 规范化后的输入、输出、总 token | 汇总真实服务报告的成本 |
| `provider_model` | 响应里报告的模型标识 | 便于诊断网关与模型配置 |
| `diagnostics` | 成功那次尝试的用量回执与精简诊断（均已脱敏） | 上下文预算用回执校准下一次的估算，见 [第 06 章](06-context-budget.md) |
| `raw` | 模型返回的原生结构 | 下一轮可能必须带回的协议状态 |

为什么 `calls` 和 `raw` 都存在？前者回答“要执行什么”，后者回答“模型这一轮在协议上说了什么”。例如推理模型返回的加密状态、签名块，不能从 `ToolCall` 反向重建；Chat Completions 的助手消息可能带 `reasoning_content`，`raw` 保存整条消息，下一次请求把它带回去。

这里的 `raw` 不是未经处理的完整 HTTP 包，而是协议解析器选出的回复结构。认证请求头不会被当作聊天历史保存。

## 核心代码：请求构造只有一个入口

下面是 `ModelClient._complete()` 的实际节选（字段名按当前实现）：

```python
payload = request_payload(
    messages,
    tools,
    protocol=protocol,
    model=self.settings.model,
    output_tokens=self.settings.effective_output_tokens,
    prompt_cache_key=prompt_cache_key,
)
```

`prompt_cache_key` 取会话编号，只写进 Responses 的请求体，作为同一会话的请求共用前缀缓存的提示；Chat 和 Anthropic 的请求体没有这个字段（设计见 [context-cache-plan.md](../context-cache-plan.md)）。

上下文预算也使用同一个 `request_payload()` 估算长度。这样计数时看到的不仅是消息正文，还包括工具定义和协议包装，减少“本地说能放下，实际发出的 JSON 却更大”的错位。

`output_tokens` 在不同协议里写成 `max_output_tokens` 或 `max_tokens`。它是允许模型生成的上限，不能和服务最终报告的 `usage.output_tokens` 混淆。`effective_output_tokens` 是经过 profile、显式设置和默认值解析后的实际上限；代码节选不能用原始 `settings.output_tokens` 代替它。

工具定义的包装也随协议而变：Responses 直接放列表，Chat 外面套一层 `{"type": "function", "function": {...}}`，Anthropic 把 `parameters` 改名为 `input_schema`。定义本身进入这里之前已被 `compact_schema` 去掉自动生成的样板，核心工具定义从 1,975 降到 1,638 个 token（[v15 设计说明](../harness-v15.md)第 3 节），这对把定义原样塞进提示的本地开源模型尤其要紧。

## Provider、profile 与模型切换

`Settings.provider` 保存连接身份，`protocol` 选择编解码分支；profile 提供模型窗口、输入/输出预算和默认协议。显式切换 `model` 时重新解析有效预算。恢复默认核对身份与配置；`allow_model_change=True` 可转换文本和工具历史，服务特定的推理密文、签名块仍属于原协议。

DeepSeek 官方接口和本地 vLLM 服务使用 `protocol="chat"`，`base_url` 指向 API 根地址；本地无认证服务可设 `auth="none"`，见[配置说明](../configuration.md)。Responses 支持流式输出，CLI 交互入口默认启用；Chat 和 Anthropic 当前走非流式路径。流式 delta 用于预览，完整回复通过协议检查后才执行工具。

## 实验：同一段历史转换成三种协议

这次只构造 JSON，不发送网络请求。`model="lesson-model"` 只是占位名称，不要求远程有这个模型。

```python
# 可运行实验
import copy
import json

from aster.messages import ContextHistoryError, history_groups
from aster.providers.codec import request_payload
from aster.tools import definitions

history = [
    {"role": "system", "content": "You are a coding assistant."},
    {"role": "user", "content": "Read hello.txt."},
    {"type": "function_call", "call_id": "read-1", "name": "read_file",
     "arguments": json.dumps({"path": "hello.txt"})},
    {"type": "function_call_output", "call_id": "read-1", "output": "hello"},
]
tools = definitions(["read_file"])

for protocol in ("responses", "chat", "anthropic"):
    payload = request_payload(
        history, tools, protocol=protocol, model="lesson-model", output_tokens=512
    )
    wire_history = payload.get("input", payload.get("messages"))
    groups = history_groups(wire_history)
    print(protocol, "消息条目:", len(wire_history), "完整组:", len(groups))
    assert "read-1" in json.dumps(payload)
    if protocol == "chat":
        assert wire_history[-1]["tool_call_id"] == "read-1"
    elif protocol == "anthropic":
        assert wire_history[-1]["content"][0]["tool_use_id"] == "read-1"
    else:
        assert wire_history[-1]["call_id"] == "read-1"

broken = copy.deepcopy(history)
del broken[2]  # 只留下结果，删掉调用。
try:
    history_groups(broken)
except ContextHistoryError as exc:
    print("损坏历史被拒绝:", str(exc).split(":")[0])
else:
    raise AssertionError("孤立结果不应被接受")
```

预期：Responses 与 Chat 都是 4 个消息条目、3 个完整组；Anthropic 把 system 放到顶层，因而是 3 个条目、2 个组。最后打印 `orphan_tool_output`。条目数不同，但调用 `read-1` 与结果的关系没有变化。

## 历史怎样变成模型请求

Runtime 保存模型的原生回复与工具结果的 JSON 记录；组装请求时，[render_prompt](../../src/aster/context/prompt.py) 将工具结果渲染成文本，再交给 `request_payload` 转为供应商格式。完整数据流见[第 05 章](05-state-and-evidence.md)。

渲染需要从调用侧查到工具名：Responses 读 `function_call.name`，Chat 读 `tool_calls[].function.name`，Anthropic 读 `tool_use.name`。例如 `read_file` 需要续读行号，空搜索需要 `[no matches]`，写入工具则已有自己的改动说明。

早期实现只解析 Responses 的工具名。2026-10-19 在 DeepSeek 轨迹中发现，Chat 结果被套上通用渲染规则，读取续页提示也会缺失；`8d5dbe1` 补齐了三种形态。这说明协议适配还延伸到历史读取与展示，入口 JSON 合法只是其中一环。

## 出错时怎样处理

客户端重试处理传输故障，Runtime 恢复处理输出截断，两者的计数和目的不同。

| 层次 | 触发条件 | 处理 |
|---|---|---|
| 客户端 | 连接中断、部分暂时性 HTTP 错误、已知过载或限流码 | 短暂退避，同一逻辑请求最多尝试 3 次 |
| 客户端 | 空响应 | 在上述上限内额外尝试至多一次 |
| Responses 流式 | 未收到终止事件就断开 | 记为 `transport / sse_missing_terminal`，按同一上限重试 |
| 协议解析 | 参数不是有效 JSON、未知终止原因 | `invalid_response` |
| Runtime | 输出达到上限 | 追加简短提示，每次运行最多恢复 2 次 |
| Runtime | 内容过滤、Anthropic `pause_turn` 等其他未完成原因 | 停止本次运行 |

已知暂时性服务错误按错误码识别，规则位于 [providers/errors.py](../../src/aster/providers/errors.py)。重试沿用本次请求内容，工具在完整回复之后执行。失败尝试已报告的 usage 仍累计，缺失用量记为未知，便于评测时正确核算成本。

### 输出上限：三种协议，一种恢复路径

| 协议 | 供应商字段 | `ProviderError.code` |
|---|---|---|
| `responses` | `status=incomplete`，原因 `max_output_tokens` | `responses:max_output_tokens` |
| `chat` | `finish_reason=length` | `chat:length` |
| `anthropic` | `stop_reason=max_tokens` | `anthropic:max_tokens` |

`check_completion()` 在解析动作前检查终止字段。上表的回复归为 `incomplete_response`，整条内容不进入执行和历史；Runtime 的 `OUTPUT_LIMIT_CODES` 接住这三种错误，提示模型简短推理并给出下一步。连续用完恢复次数后，运行以 `provider_error` 停止，错误类别仍是 `incomplete_response`。

2026-10-19（`7abdba4`）之前，截断会直接停止运行。DeepSeek 评测暴露了推理耗尽输出预算、来不及给动作的情况，因此加入这条恢复路径，见 [harness-v15.md](../harness-v15.md) 第 4 节。

## 练习与核对

1. 把实验中的调用编号改为空字符串，或重复追加同一条结果，会发生什么？
2. 为什么恢复时能转换文本和工具历史，却不能转换加密推理状态？
3. Chat 回复里有一个完整调用，但 `finish_reason=length`，会执行吗？

<details>
<summary>参考答案</summary>

1. `history_groups()` 分别报告 `invalid_tool_id` 或 `duplicate_tool_output`，请求组装在本地停止。
2. 文本和工具调用有共同结构；推理密文与签名由各服务解释，转换器没有对应的跨协议表示。
3. 整条回复按未完成处理，Runtime 追加提示后重新请求。

</details>

三种适配的本地检查见 [test_provider_events.py](../../tests/test_provider_events.py)、[test_protocol_pairing.py](../../tests/test_protocol_pairing.py) 和 [test_streaming.py](../../tests/test_streaming.py)。真实评测长期使用 GPT Responses，2026-10-19 起加入 DeepSeek 官方 Chat Completions（非流式），见[评测报告第 15 节](../../evaluations/harness-v12-20261017/REPORT.md)；Anthropic 目前由本地契约测试覆盖。

上一章：[01｜Agent 循环](01-agent-loop.md) · [目录](README.md) · 下一章：[03｜工具系统](03-tool-system.md)
