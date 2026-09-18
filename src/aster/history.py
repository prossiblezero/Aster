"""Portable transcript conversion; the caller retains native evidence separately."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .provider import ContextHistoryError, history_groups

PROTOCOLS = {"responses", "chat", "anthropic"}


@dataclass(frozen=True)
class CanonicalHistory:
    messages: list[dict[str, Any]]
    source: dict[str, str]
    omitted_reasoning: int


def _fields(value: dict, allowed: set[str]) -> None:
    if not isinstance(value, dict) or value.keys() - allowed:
        raise ContextHistoryError("unsupported_history_fields")


def _string(value: Any, name: str, *, nonempty: bool = False) -> str:
    if not isinstance(value, str) or (nonempty and not value.strip()):
        raise ContextHistoryError(f"invalid_history_{name}")
    return value


def _call(call_id: Any, name: Any, arguments: Any) -> dict:
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
        except ValueError as exc:
            raise ContextHistoryError("invalid_history_arguments") from exc
    else:
        parsed = arguments
    if not isinstance(parsed, dict):
        raise ContextHistoryError("invalid_history_arguments")
    try:
        encoded = json.dumps(parsed, ensure_ascii=False, allow_nan=False)
    except (ValueError, TypeError) as exc:
        raise ContextHistoryError("invalid_history_arguments") from exc
    return {
        "type": "function_call",
        "call_id": _string(call_id, "call_id", nonempty=True),
        "name": _string(name, "tool_name", nonempty=True),
        "arguments": encoded,
    }


def _content(content: Any, *, images: bool = False) -> str | list[dict]:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        raise ContextHistoryError("unsupported_history_content")
    result = []
    for block in content:
        if not isinstance(block, dict):
            raise ContextHistoryError("unsupported_history_content")
        kind = block.get("type")
        if not isinstance(kind, str):
            raise ContextHistoryError("unsupported_history_content")
        if kind in {"text", "input_text", "output_text"}:
            _fields(
                block, {"type", "text", "annotations", "citations", "logprobs", "cache_control"}
            )
            if block.get("annotations") or block.get("citations"):
                raise ContextHistoryError("unsupported_history_annotations")
            result.append({"type": "input_text", "text": _string(block.get("text"), "text")})
        elif kind == "refusal":
            _fields(block, {"type", "refusal"})
            result.append({"type": "input_text", "text": _string(block.get("refusal"), "refusal")})
        elif kind in {"input_image", "image_url", "image"}:
            if not images:
                raise ContextHistoryError("unsupported_user_or_assistant_image")
            if kind == "image":
                _fields(block, {"type", "source", "cache_control"})
                source = block.get("source")
                _fields(source, {"type", "media_type", "data"})
                if source.get("type") != "base64":
                    raise ContextHistoryError("unsupported_history_image_source")
                media = _string(source.get("media_type"), "image_media", nonempty=True)
                data = _string(source.get("data"), "image_data", nonempty=True)
                url = f"data:{media};base64,{data}"
            elif kind == "image_url":
                _fields(block, {"type", "image_url"})
                image = block.get("image_url")
                _fields(image, {"url", "detail"})
                if image.get("detail", "auto") != "auto":
                    raise ContextHistoryError("unsupported_history_image_detail")
                url = _string(image.get("url"), "image_url")
            else:
                _fields(block, {"type", "image_url", "detail"})
                if block.get("detail", "auto") != "auto":
                    raise ContextHistoryError("unsupported_history_image_detail")
                url = _string(block.get("image_url"), "image_url")
            if not url.startswith("data:image/") or ";base64," not in url:
                raise ContextHistoryError("unsupported_history_image_source")
            result.append({"type": "input_image", "image_url": url})
        else:
            raise ContextHistoryError(f"unsupported_history_content:{kind}")
    if images:
        return result
    return "".join(block["text"] for block in result)


def _output(call_id: Any, content: Any, *, is_error: bool | None = None) -> dict:
    output = _content(content, images=True)
    if is_error is not None:
        if type(is_error) is not bool:
            raise ContextHistoryError("invalid_history_tool_error")
        status = "error" if is_error else "success"
        if isinstance(output, str):
            output = json.dumps({"status": status, "content": output}, ensure_ascii=False)
        else:
            output.insert(0, {"type": "input_text", "text": json.dumps({"status": status})})
    return {
        "type": "function_call_output",
        "call_id": _string(call_id, "call_id", nonempty=True),
        "output": output,
    }


def canonicalize(
    history: list[dict], *, provider: str = "", protocol: str, model: str = ""
) -> CanonicalHistory:
    """Convert complete exchanges without mutating input or copying metadata onto the wire.

    Native thinking/signatures are intentionally omitted and counted. Images are
    supported only in tool results, the common surface of the current adapters.
    Unsupported semantic content fails before the caller changes session state.
    """
    if protocol not in PROTOCOLS:
        raise ContextHistoryError("unsupported_history_protocol")
    source = {
        "provider": _string(provider, "provider"),
        "protocol": protocol,
        "model": _string(model, "model"),
    }
    if not isinstance(history, list) or any(not isinstance(item, dict) for item in history):
        raise ContextHistoryError("invalid_history")
    try:
        history_groups(history)
    except (AttributeError, KeyError, TypeError) as exc:
        raise ContextHistoryError("invalid_history_structure") from exc
    messages, omitted = [], 0
    for item in history:
        kind, role = item.get("type"), item.get("role")
        if kind is not None and not isinstance(kind, str):
            raise ContextHistoryError("unsupported_history_item")
        if item.get("status") is not None and item["status"] != "completed":
            raise ContextHistoryError("incomplete_history_item")
        if kind == "reasoning":
            _fields(
                item, {"type", "id", "status", "summary", "content", "encrypted_content", "role"}
            )
            omitted += 1
        elif kind == "function_call":
            _fields(item, {"type", "id", "status", "call_id", "name", "arguments"})
            messages.append(_call(item.get("call_id"), item.get("name"), item.get("arguments")))
        elif kind == "function_call_output":
            _fields(item, {"type", "id", "status", "call_id", "output"})
            messages.append(_output(item.get("call_id"), item.get("output")))
        elif kind not in {None, "message"}:
            raise ContextHistoryError(f"unsupported_history_item:{kind}")
        elif role == "tool":
            _fields(item, {"role", "tool_call_id", "content", "name"})
            messages.append(_output(item.get("tool_call_id"), item.get("content")))
        elif role in {"system", "user", "assistant"}:
            _fields(
                item,
                {
                    "type",
                    "id",
                    "status",
                    "role",
                    "content",
                    "tool_calls",
                    "refusal",
                    "reasoning_content",
                    "reasoning",
                    "annotations",
                    "phase",
                },
            )
            phase = item.get("phase")
            if "phase" in item and (
                role != "assistant" or phase not in (None, "commentary", "final_answer")
            ):
                raise ContextHistoryError("invalid_history_phase")
            if item.get("annotations"):
                raise ContextHistoryError("unsupported_history_annotations")
            if role != "assistant" and any(
                item.get(key) for key in ("tool_calls", "refusal", "reasoning_content", "reasoning")
            ):
                raise ContextHistoryError("invalid_history_assistant_fields")
            omitted += sum(item.get(key) is not None for key in ("reasoning_content", "reasoning"))
            content = item.get("content")
            blocks = []
            calls = []
            if isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        raise ContextHistoryError("unsupported_history_content")
                    block_kind = block.get("type")
                    if not isinstance(block_kind, str):
                        raise ContextHistoryError("unsupported_history_content")
                    if block_kind in {"thinking", "redacted_thinking"} and role == "assistant":
                        _fields(block, {"type", "thinking", "signature", "data"})
                        omitted += 1
                    elif block_kind == "tool_use" and role == "assistant":
                        _fields(block, {"type", "id", "name", "input", "cache_control"})
                        calls.append(_call(block.get("id"), block.get("name"), block.get("input")))
                    elif block_kind == "tool_result" and role == "user":
                        _fields(
                            block, {"type", "tool_use_id", "content", "is_error", "cache_control"}
                        )
                        messages.append(
                            _output(
                                block.get("tool_use_id"),
                                block.get("content", ""),
                                is_error=block.get("is_error"),
                            )
                        )
                    else:
                        blocks.append(block)
                text = _content(blocks)
            elif content is None and role == "assistant":
                text = ""
            else:
                text = _content(content)
            if item.get("refusal") is not None:
                text += _string(item["refusal"], "refusal")
            for call in item.get("tool_calls") or []:
                _fields(call, {"type", "id", "function"})
                if call.get("type") != "function":
                    raise ContextHistoryError("unsupported_history_tool_type")
                function = call.get("function")
                _fields(function, {"name", "arguments"})
                calls.append(_call(call.get("id"), function.get("name"), function.get("arguments")))
            if (
                text
                or phase is not None
                or (role in {"system", "user"} and not isinstance(content, list))
            ):
                message = {"role": role, "content": text}
                if phase is not None:
                    # Responses uses phase to distinguish progress from the final answer.
                    message["phase"] = phase
                messages.append(message)
            messages.extend(calls)
        else:
            raise ContextHistoryError(f"unsupported_history_role:{role}")
    history_groups(messages)
    return CanonicalHistory(messages=messages, source=source, omitted_reasoning=omitted)
