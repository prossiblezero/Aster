# 02｜把不同模型接到同一个循环

上一章：[Agent 循环](01-agent-loop.md) · [目录](README.md) · 下一章：[工具系统](03-tool-system.md)

> 统一运行时动作，保留协议里不能随意改写的历史。

## 为什么不能只换一个 URL

同样是“读取文件”，Responses API、Chat Completions 和 Anthropic Messages 的 JSON 形状不同。有的把调用写成独立条目，有的放在助手消息的 `tool_calls` 里，有的放在 `content` 的 `tool_use` 块里。

如果循环内部到处判断“当前是哪家模型”，工具、安全、记忆都会被接口格式牵着走。传输与请求记账分别位于 `providers/client.py`、`providers/streaming.py` 和 `providers/usage.py`；Aster 把纯协议适配收在 [providers/codec.py](../../src/aster/providers/codec.py)：请求前转换协议，返回后解析成统一 `ModelReply`，Runtime 只处理 `ToolCall`。

```text
Runtime.prepare_request()
        ↓ 消息 + 工具定义
request_payload() ── 按 protocol 序列化
        ↓
ModelClient._post() ── HTTPX 发送请求
        ↓
协议解析函数 ── ModelReply(text, calls, usage, raw)
        ↓
Runtime 执行 calls，保存 raw，追加工具结果
```

HTTPX 是这里实际使用的 HTTP 客户端。代码没有依赖 OpenAI 或 Anthropic 的官方 Python SDK；支持它们的接口协议与使用官方 SDK 是两件事。

## 对照一次调用与结果

假设模型提出 `read_file(path="hello.txt")`，编号为 `read-1`。三种协议的对应位置如下：

| 协议 | 工具调用 | 对应结果 |
|---|---|---|
| `responses` | `type=function_call`，编号在 `call_id` | `function_call_output`，同一个 `call_id` |
| `chat` | 助手消息的 `tool_calls`，编号在 `id` | `role=tool`，编号在 `tool_call_id` |
| `anthropic` | 助手 `content` 内的 `tool_use`，编号在 `id` | 用户 `content` 内的 `tool_result`，编号在 `tool_use_id` |

这些编号是引用关系，不是装饰字段。结果存在但对应调用被删掉，会形成孤立结果；同一编号重复出现，也不能靠文本看起来合理就接受。`history_groups()` 负责验证这种结构，并把完整交互分组，供上下文压缩使用。

## 读懂 ModelReply 的五个字段

| 字段 | 作用 | 为什么需要 |
|---|---|---|
| `text` | 可展示的回复文字 | 用于解释或最终回答 |
| `calls` | 标准化的 `ToolCall` 列表 | 工具执行不依赖供应商格式 |
| `usage` | 规范化后的输入、输出、总 token | 汇总真实服务报告的成本 |
| `provider_model` | 响应里报告的模型标识 | 便于诊断网关与模型配置 |
| `raw` | 模型返回的原生结构 | 下一轮可能必须带回的协议状态 |

为什么 `calls` 和 `raw` 都存在？前者回答“要执行什么”，后者回答“模型这一轮在协议上说了什么”。例如推理模型返回的加密状态、签名块，不能从 `ToolCall` 反向重建。

这里的 `raw` 不是未经处理的完整 HTTP 包，而是协议解析器选出的回复结构。认证请求头不会被当作聊天历史保存。

## 核心代码：请求构造只有一个入口

下面是 `ModelClient.complete()` 的实际节选（字段名按当前实现）：

```python
payload = request_payload(
    messages,
    tools,
    protocol=protocol,
    model=self.settings.model,
    output_tokens=self.settings.effective_output_tokens,
)
```

上下文预算也使用同一个 `request_payload()` 估算长度。这样计数时看到的不仅是消息正文，还包括工具定义和协议包装，减少“本地说能放下，实际发出的 JSON 却更大”的错位。

`output_tokens` 在不同协议里写成 `max_output_tokens` 或 `max_tokens`。它是允许模型生成的上限，不能和服务最终报告的 `usage.output_tokens` 混淆。`effective_output_tokens` 是经过 profile、显式设置和默认值解析后的实际上限；代码节选不能用原始 `settings.output_tokens` 代替它。

## Provider、profile 与模型切换

`Settings.provider` 是身份与连接配置元数据，实际适配分支由 `protocol` 选择（当前代码在 `providers/codec.py` 中按 `responses`、`chat`、`anthropic` 分支处理）。profile 提供可移植的模型窗口、输入/输出预算和默认协议；显式 `model` 切换会重新解析有效预算。默认恢复会检查保存的身份，配置不一致时拒绝；显式 `allow_model_change=True` 才能走受限的模型转换路径。它可以把可移植的文本/工具历史 canonicalize 到另一协议，但不能迁移服务特定的原生推理状态，也不能改变安全或能力设置。`provider_model` 只是服务响应报告的身份字段，不能证明网关真的使用了所请求的内部模型。

Responses 的流式路径目前由 `streaming.complete_responses` 实现，默认关闭，且不能据此推断 Chat 或 Anthropic 也支持流式。delta 事件是临时预览；只有完整成功响应和调用配对确认后才执行工具。空响应最多只额外重试一次，且非流式 `_post` 与 Responses streaming 都受总尝试最多 3 次的边界约束。空响应、协议解析失败、未知 usage 和传输错误都会进入诊断；unknown usage 不是 0，也不能从诊断中的尝试数推导成功。流式预览本身不是已执行动作的证据。

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

## 出错时怎样处理

`ModelClient._post()` 最多尝试 3 次请求；空响应最多触发其中一次额外尝试。Responses streaming 也遵守同一逻辑请求上限。网络超时、部分暂时性 HTTP 错误，以及 HTTP 200 内明确报告的服务过载或限流，会短暂退避后重试。HTTP 成功并不代表生成成功：例如 SSE 的 `response.failed` 携带 `error.code=server_error`，应归为 `transient_provider`，不能当作普通截断直接停止。共享规则只识别 `server_error`、`overloaded_error`、`rate_limit_exceeded`、`rate_limit_error`，不靠错误消息中的关键词猜测。认证错误、拒答、取消和输出截断仍不会因此重试。

SSE 在收到终止事件前结束，归为传输中断（`transport / sse_missing_terminal`），按同一上限重新请求。重试保持本次请求内容，已成功执行的工具不会重放，半截调用不会执行；没有收到 usage 就记录为未知。模型明确返回 `response.incomplete`、例如耗尽输出长度，则仍是不可重试的生成截断。调用方缩小 `ModelClient.max_attempts` 时，流式与非流式路径都遵守该上限。恢复后保留已写文件的回归场景见 [test_streaming.py](../../tests/test_streaming.py)。

最终错误用 `ProviderError` 记录类别、HTTP 状态、错误代码、尝试次数，输出经过脱敏；失败尝试已报告的 usage 会累加，缺失用量仍记为未知。诊断中的“尝试过”不等于成功，unknown usage 不按 0 计，也不认证服务端内部模型身份。有限退避参考了 [pi 的自动重试](https://github.com/earendil-works/pi/blob/f1b2e77f5b13b2a199b1052cb79c235451afe7d7/packages/coding-agent/src/core/agent-session.ts#L3699)，Aster 保持自身最多三次请求的边界。

| 层次 | 例子 | 当前行为 |
|---|---|---|
| 传输 | 网络连接中断 | 有限重试，耗尽后停止本次运行 |
| HTTP | 429 限流、503 服务不可用 | 有限退避重试 |
| 响应内容 | HTTP 200 内的已知过载、限流错误码 | 与传输重试共用三次上限，失败响应中的工具不执行 |
| 协议回复 | 参数不是有效 JSON | `invalid_response` |
| 生成未完成 | 输出上限截断工具参数 | `incomplete_response`，不执行半截调用 |
| 工具执行 | 文件不存在 | 返回工具结果，由循环决定后续 |

重试的是生成请求。工具副作用要等完整回复后才执行；这里没有“任何异常都把上次写文件再做一次”的逻辑。重复生成可能仍有远程费用，这套实现不承诺网络层的全局恰好一次语义。

## 练习与边界

把实验中的 `call_id` 改成空字符串，或再追加一次相同结果，分别会触发 `invalid_tool_id` 或 `duplicate_tool_output`。这些错误应该在向服务发请求前暴露。

思考：能不能把 Responses 的加密推理字段翻译为 Anthropic 的签名？不能。它们是服务特定的原生状态；默认恢复要求身份和有效配置一致，显式模型变更只允许转换可移植文本/工具历史，仍不能把换模型供应商理解成无损迁移一个正在进行的原生会话。

这三种适配都有本地协议测试，但项目已归档的真实模型评测走的是 Responses。阅读 [test_provider_events.py](../../tests/test_provider_events.py) 和 [test_protocol_pairing.py](../../tests/test_protocol_pairing.py) 能看到本地契约；不要把契约测试写成三个服务都完成了同等规模的线上验证。

上一章：[01｜Agent 循环](01-agent-loop.md) · [目录](README.md) · 下一章：[03｜工具系统](03-tool-system.md)
