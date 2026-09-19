"""Pure wire payload and authoritative terminal reply conversion."""
from __future__ import annotations

import json

from ..messages import ContextHistoryError, history_groups
from ..schema import ModelReply, ToolCall
from .errors import ProviderError


def _validated_reply(text, calls, raw, data, usage, *, opaque=False):
    if not isinstance(text, str) or not isinstance(data.get("model", ""), str):
        raise ValueError("invalid response text/model")
    ids = [call.call_id for call in calls]
    if len(ids) != len(set(ids)) or any(not key.strip() for key in ids):
        raise ValueError("duplicate or empty tool call ID")
    if not text.strip() and not calls:
        raise ProviderError(
            "Response contains no supported text or tool call"
            if opaque
            else "Provider returned an empty response",
            category="unsupported_output" if opaque else "empty_response",
            usage=usage,
        )
    return ModelReply(
        text=text, calls=calls, usage=usage or {}, raw=raw, provider_model=data.get("model", "")
    )


def _refusal(usage):
    raise ProviderError("Provider explicitly refused the response", category="refusal", usage=usage)


def _text(content):
    if isinstance(content, str):
        return content
    return "\n".join(block.get("text", "") for block in content or [] if isinstance(block, dict))


def _chat_messages(messages):
    result, images = [], []
    for item in messages:
        kind = item.get("type")
        if kind == "function_call_output":
            output = item.get("output", "")
            result.append(
                {"role": "tool", "tool_call_id": item["call_id"], "content": _text(output)}
            )
            if isinstance(output, list):
                images.extend(
                    {"type": "image_url", "image_url": {"url": block["image_url"]}}
                    for block in output
                    if block.get("type") == "input_image"
                )
        elif kind == "function_call":
            call = {
                "id": item["call_id"],
                "type": "function",
                "function": {
                    "name": item["name"],
                    "arguments": item["arguments"],
                },
            }
            if result and result[-1].get("role") == "assistant":
                result[-1].setdefault("tool_calls", []).append(call)
            else:
                result.append({"role": "assistant", "content": None, "tool_calls": [call]})
        elif kind != "reasoning":
            message = {
                key: value
                for key, value in item.items()
                if key
                in {
                    "role",
                    "content",
                    "tool_calls",
                    "tool_call_id",
                    "reasoning_content",
                    "reasoning",
                }
            }
            if isinstance(message.get("content"), list):
                message["content"] = _text(message["content"])
            result.append(message)
    if images:
        result.append({"role": "user", "content": images})
    return result


def _anthropic_messages(messages):
    system, result = [], []
    for item in messages:
        if item.get("role") == "system":
            system.append(_text(item.get("content", "")))
            continue
        kind = item.get("type")
        if kind == "function_call_output":
            output = item.get("output", "")
            content = output
            if isinstance(output, list):
                content = []
                for block in output:
                    if block.get("type") == "input_image":
                        media, data = block["image_url"].split(";base64,", 1)
                        content.append(
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": media.removeprefix("data:"),
                                    "data": data,
                                },
                            }
                        )
                    else:
                        content.append({"type": "text", "text": block.get("text", "")})
            role, blocks = (
                "user",
                [{"type": "tool_result", "tool_use_id": item["call_id"], "content": content}],
            )
        elif kind == "function_call":
            role, blocks = (
                "assistant",
                [
                    {
                        "type": "tool_use",
                        "id": item["call_id"],
                        "name": item["name"],
                        "input": json.loads(item["arguments"]),
                    }
                ],
            )
        elif kind == "reasoning":
            continue  # Responses reasoning is opaque and cannot be translated across providers.
        else:
            role = item.get("role", "assistant")
            content = item.get("content", "")
            blocks = [{"type": "text", "text": content}] if isinstance(content, str) else content
        if result and result[-1]["role"] == role:
            result[-1]["content"].extend(blocks)
        else:
            result.append({"role": role, "content": list(blocks)})
    return "\n\n".join(system), result


def request_payload(messages, tools, *, protocol="responses", model="", output_tokens=2048):
    """Shared by ContextBuilder and the transport, so budgeting sees the actual request shape."""
    history_groups(messages)
    if protocol in {"chat", "anthropic"} and any(
        item.get("phase") is not None for item in messages
    ):
        # These protocols have no equivalent assistant-phase field. Fail preflight
        # instead of turning intermediate commentary into an unlabelled answer.
        raise ContextHistoryError("unsupported_history_phase_for_protocol")
    if protocol == "chat":
        history = _chat_messages(messages)
        payload = {
            "model": model,
            "messages": history,
            "max_tokens": output_tokens,
            "tools": [
                {
                    "type": "function",
                    "function": {key: value for key, value in tool.items() if key != "type"},
                }
                for tool in tools
            ],
        }
    elif protocol == "anthropic":
        system, history = _anthropic_messages(messages)
        payload = {
            "model": model,
            "system": system,
            "messages": history,
            "max_tokens": output_tokens,
            "tools": [
                {
                    "name": t["name"],
                    "description": t["description"],
                    "input_schema": t["parameters"],
                }
                for t in tools
            ],
        }
    else:
        payload = {
            "model": model,
            "input": messages,
            "tools": tools,
            "max_output_tokens": output_tokens,
            "store": False,
        }
    history_groups(payload.get("input", payload.get("messages", [])))
    return payload


def parse_reply(data, usage, protocol):
    try:
        if not isinstance(data, dict):
            raise ValueError("response must be an object")
        return {
            "responses": responses_reply,
            "chat": chat_reply,
            "anthropic": anthropic_reply,
        }[protocol](data, usage)
    except (ValueError, KeyError, TypeError, IndexError, AttributeError) as exc:
        raise ProviderError(
            "Malformed model response or tool arguments",
            category="invalid_response",
            usage=usage,
        ) from exc


def check_completion(data, protocol, usage):
    if protocol == "responses":
        reason = data.get("status", "")
        if reason not in {
            None,
            "",
            "completed",
            "incomplete",
            "failed",
            "cancelled",
            "queued",
            "in_progress",
        }:
            raise ValueError("unknown response status")
        incomplete = reason in {"incomplete", "failed", "cancelled", "queued", "in_progress"}
        if reason == "incomplete":
            details = data.get("incomplete_details") or {}
            cause = details.get("reason") if isinstance(details, dict) else None
            if cause in {"max_output_tokens", "content_filter"}:
                reason = cause
    elif protocol == "chat":
        reason = data["choices"][0].get("finish_reason", "")
        if reason not in {
            None,
            "",
            "stop",
            "tool_calls",
            "length",
            "content_filter",
            "function_call",
        }:
            raise ValueError("unknown finish reason")
        incomplete = reason in {"length", "content_filter"}
    else:
        reason = data.get("stop_reason", "")
        if reason not in {
            None,
            "",
            "end_turn",
            "tool_use",
            "stop_sequence",
            "max_tokens",
            "pause_turn",
            "refusal",
        }:
            raise ValueError("unknown stop reason")
        incomplete = reason in {"max_tokens", "pause_turn"}
    if incomplete:
        raise ProviderError(
            "Provider stopped before a complete model response",
            category="incomplete_response",
            code=f"{protocol}:{reason}",
            usage=usage,
        )


def responses_reply(data, usage=None):
    check_completion(data, "responses", usage)
    calls, texts, opaque = [], [], False
    output = data.get("output", [])
    if not isinstance(output, list) or data.get("error"):
        raise ValueError("invalid output")
    for item in output:
        if not isinstance(item, dict) or item.get("status") not in {None, "completed"}:
            raise ValueError("invalid output item")
        kind = item.get("type")
        if kind == "function_call":
            calls.append(
                ToolCall(
                    name=item["name"],
                    arguments=json.loads(item["arguments"]),
                    call_id=item.get("call_id", item.get("id", "")),
                )
            )
        elif kind == "message":
            if item.get("role", "assistant") != "assistant" or not isinstance(
                item.get("content"), list
            ):
                raise ValueError("invalid output message")
            for block in item["content"]:
                if not isinstance(block, dict):
                    raise ValueError("invalid content block")
                if block.get("type") == "refusal":
                    _refusal(usage)
                if block.get("type") not in {"output_text", "text"}:
                    raise ProviderError(
                        "Unsupported response content",
                        category="unsupported_output",
                        usage=usage,
                    )
                if not isinstance(block.get("text"), str):
                    raise ValueError("invalid text")
                texts.append(block["text"])
        elif kind == "reasoning":
            opaque = True
        else:
            raise ProviderError(
                "Unsupported response output", category="unsupported_output", usage=usage
            )
    text = "".join(texts)
    # Legacy compatible endpoints may supply only the top-level output_text shortcut.
    if not output:
        text = data.get("output_text", "")
    return _validated_reply(text, calls, output, data, usage, opaque=opaque)


def chat_reply(data, usage=None):
    check_completion(data, "chat", usage)
    choices = data["choices"]
    if not isinstance(choices, list) or len(choices) != 1:
        raise ValueError("expected one response choice")
    message = choices[0]["message"]
    if message.get("role", "assistant") != "assistant":
        raise ValueError("invalid assistant role")
    if message.get("refusal"):
        _refusal(usage)
    if (
        message.get("audio")
        or message.get("function_call")
        or isinstance(message.get("content"), list)
    ):
        raise ProviderError(
            "Unsupported chat output", category="unsupported_output", usage=usage
        )
    calls = []
    for item in message.get("tool_calls", []):
        if item.get("type", "function") != "function":
            raise ProviderError(
                "Unsupported chat tool type", category="unsupported_output", usage=usage
            )
        calls.append(
            ToolCall(
                name=item["function"]["name"],
                arguments=json.loads(item["function"]["arguments"]),
                call_id=item["id"],
            )
        )
    return _validated_reply(
        message.get("content") if message.get("content") is not None else "",
        calls,
        [message],
        data,
        usage,
        opaque=bool(message.get("reasoning_content") or message.get("reasoning")),
    )


def anthropic_reply(data, usage=None):
    check_completion(data, "anthropic", usage)
    if data.get("stop_reason") == "refusal":
        _refusal(usage)
    text, calls, opaque = "", [], False
    content = data.get("content", [])
    if not isinstance(content, list):
        raise ValueError("invalid content")
    for block in content:
        kind = block.get("type")
        if kind == "text":
            text += block["text"]
        elif kind == "tool_use":
            calls.append(
                ToolCall(name=block["name"], arguments=block["input"], call_id=block["id"])
            )
        elif kind in {"thinking", "redacted_thinking"}:
            opaque = True
        else:
            raise ProviderError(
                "Unsupported Anthropic output", category="unsupported_output", usage=usage
            )
    return _validated_reply(
        text, calls, [{"role": "assistant", "content": content}], data, usage, opaque=opaque
    )
