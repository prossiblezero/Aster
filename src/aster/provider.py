"""Small protocol adapters and bounded retries around one model request."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

import httpx

from .config import Settings
from .evidence import Redactor
from .schema import ModelReply, ToolCall


class ContextHistoryError(ValueError):
    pass


def history_groups(history):
    """Validate complete tool exchanges and keep their reasoning in the same cut group."""
    groups, group, pending, seen, returned = [], [], set(), set(), set()
    accepting_calls = False
    for item in history:
        kind, role = item.get("type"), item.get("role")
        calls, outputs = [], []
        if kind == "function_call":
            calls = [item.get("call_id")]
        elif kind == "function_call_output":
            outputs = [item.get("call_id")]
        if item.get("tool_calls"):
            if role != "assistant":
                raise ContextHistoryError("invalid_tool_role: calls require an assistant message")
            calls.extend(call.get("id") for call in item["tool_calls"])
        if role == "tool":
            outputs.append(item.get("tool_call_id"))
        content = item.get("content")
        if isinstance(content, list):
            saw_other_block = False
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use":
                    if role != "assistant":
                        raise ContextHistoryError(
                            "invalid_tool_role: tool_use requires an assistant message"
                        )
                    calls.append(block.get("id"))
                elif block.get("type") == "tool_result":
                    if role != "user" or saw_other_block:
                        raise ContextHistoryError(
                            "invalid_tool_order: tool_result must lead a user message"
                        )
                    outputs.append(block.get("tool_use_id"))
                else:
                    saw_other_block = True
        if any(not isinstance(key, str) or not key.strip() for key in calls + outputs):
            raise ContextHistoryError("invalid_tool_id: tool calls and results need nonempty ids")
        if len(calls) != len(set(calls)) or seen.intersection(calls):
            raise ContextHistoryError("duplicate_tool_call: history contains a reused call id")
        if calls and outputs:
            raise ContextHistoryError(
                "invalid_tool_order: calls and results cannot share a message"
            )
        if outputs:
            for key in outputs:
                if key in returned:
                    raise ContextHistoryError("duplicate_tool_output: each call needs one result")
                if key not in pending:
                    raise ContextHistoryError(
                        "orphan_tool_output: history must retain complete exchanges"
                    )
                pending.remove(key)
                returned.add(key)
            if role == "user" and pending:
                raise ContextHistoryError("missing_tool_output: native result batch is incomplete")
            group.append(item)
            accepting_calls = False
            if not pending:
                groups.append(group)
                group = []
            continue
        if pending and not (kind == "function_call" and accepting_calls):
            raise ContextHistoryError("missing_tool_output: next turn starts before results")
        new_turn = kind == "reasoning" or role in {"user", "assistant", "system"}
        if group and not pending and new_turn:
            # Contiguous reasoning belongs to the following assistant response/call batch.
            if role in {"user", "system"} or any(x.get("type") != "reasoning" for x in group):
                groups.append(group)
                group = []
        pending.update(calls)
        seen.update(calls)
        accepting_calls = bool(calls) and kind == "function_call"
        group.append(item)
        if role in {"user", "system"}:
            groups.append(group)
            group = []
    if pending:
        raise ContextHistoryError("missing_tool_output: history ends before results")
    if group:
        groups.append(group)
    return groups


class ProviderError(RuntimeError):
    """Safe diagnostics suitable for the trace; never contains request headers/body."""

    def __init__(
        self,
        message: str,
        *,
        category="provider_error",
        status=None,
        code="",
        attempts=1,
        usage=None,
    ):
        self.details = {
            "category": category,
            "status": status,
            "code": code,
            "message": message,
            "attempts": attempts,
            "usage": dict(usage or {}),
        }
        super().__init__(json.dumps(self.details, ensure_ascii=False))


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


class ModelClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.last_usage: dict[str, int] = {}
        self.redaction_secrets = [settings.api_key.get_secret_value()]

    async def complete(
        self, messages: list[dict[str, Any]], tools: list[dict], *, on_event=None
    ) -> ModelReply:
        protocol = self.settings.protocol
        if self.settings.stream and protocol != "responses":
            raise ProviderError("Streaming currently requires Responses", category="configuration")
        payload = request_payload(
            messages,
            tools,
            protocol=protocol,
            model=self.settings.model,
            output_tokens=self.settings.effective_output_tokens,
        )
        suffix = {"responses": "/responses", "chat": "/chat/completions", "anthropic": "/messages"}[
            protocol
        ]
        headers = (
            {
                "x-api-key": self.settings.api_key.get_secret_value(),
                "anthropic-version": "2023-06-01",
            }
            if protocol == "anthropic"
            else {}
        )
        if self.settings.stream:
            from .streaming import complete_responses

            return await complete_responses(self, payload, headers, on_event)
        data = await self._post(suffix, payload, headers)
        try:
            parser = {
                "responses": self._responses_reply,
                "chat": self._chat_reply,
                "anthropic": self._anthropic_reply,
            }[protocol]
            return parser(data, self.last_usage)
        except (ValueError, KeyError, TypeError, IndexError, AttributeError) as exc:
            raise ProviderError(
                "Malformed model response or tool arguments",
                category="invalid_response",
                usage=self.last_usage,
            ) from exc

    async def _post(self, suffix: str, payload: dict, extra: dict) -> dict:
        secret = self.settings.api_key.get_secret_value()
        if not secret and self.settings.auth != "none":
            raise ProviderError("ASTER_API_KEY is empty", category="configuration")
        self.last_usage = {}
        headers = {"Content-Type": "application/json", **extra}
        if self.settings.auth != "none":
            headers["Authorization"] = f"Bearer {secret}"
        else:
            headers.pop("x-api-key", None)
        url = self.settings.base_url.rstrip("/") + suffix
        redactor = Redactor([secret])
        # Only generation requests are retried: tool side effects execute after a complete reply.
        async with httpx.AsyncClient(timeout=self.settings.request_timeout) as client:
            for attempt in range(1, 4):
                delay = 0.25 * 2 ** (attempt - 1)
                try:
                    response = await client.post(url, headers=headers, json=payload)
                except (
                    httpx.TimeoutException,
                    httpx.NetworkError,
                    httpx.RemoteProtocolError,
                ) as exc:
                    error = ProviderError(
                        type(exc).__name__, category="transport", attempts=attempt
                    )
                    if attempt == 3:
                        raise error from exc
                else:
                    try:
                        data = response.json()
                    except ValueError:
                        data = {}
                    if (
                        response.is_success
                        and isinstance(data, dict)
                        and data
                        and not data.get("error")
                    ):
                        self.last_usage = self._usage(data.get("usage"), self.settings.protocol)
                        return data
                    raw = data.get("error", {}) if isinstance(data, dict) else {}
                    raw = raw if isinstance(raw, dict) else {"message": str(raw)}
                    detail = redactor.text(
                        str(raw.get("message", "Provider returned no valid JSON response"))
                    )
                    detail = re.sub(r"https?://\S+", "[provider URL]", detail)
                    detail = re.sub(
                        r"(?i)\b(password|api[_ -]?key|authorization|secret)\s*[:=]\s*\S+",
                        r"\1=[REDACTED]",
                        detail,
                    )
                    code = redactor.text(str(raw.get("code", raw.get("type", ""))))[:120]
                    retryable = response.status_code in {408, 409, 425, 429, 500, 502, 503, 504, 524}
                    category = (
                        "transient_http"
                        if retryable
                        else ("invalid_response" if response.is_success else "request_rejected")
                    )
                    error = ProviderError(
                        detail[:800],
                        category=category,
                        status=response.status_code,
                        code=code,
                        attempts=attempt,
                    )
                    if not retryable or attempt == 3:
                        raise error
                    try:
                        delay = min(2, max(delay, float(response.headers.get("retry-after", "0"))))
                    except ValueError:
                        pass  # Date-valued Retry-After uses the same short bounded backoff.
                await asyncio.sleep(delay)
        raise error  # pragma: no cover

    @staticmethod
    def _usage(raw: dict, protocol: str = "responses") -> dict[str, int]:
        """Known token counts plus per-metric coverage; absent is unknown, not zero."""
        raw = raw if isinstance(raw, dict) else {}

        def find(*path):
            value = raw
            for key in path:
                value = value.get(key) if isinstance(value, dict) else None
            return value if type(value) is int and value >= 0 else None

        # Some compatible gateways use Responses names in Chat usage envelopes.
        inputs, outputs = find("input_tokens"), find("output_tokens")
        if inputs is None:
            inputs = find("prompt_tokens")
        if outputs is None:
            outputs = find("completion_tokens")
        counts = {"input_tokens": inputs, "output_tokens": outputs}
        if protocol == "anthropic":
            counts.update(
                uncached_input_tokens=inputs,
                cache_read_input_tokens=find("cache_read_input_tokens"),
                cache_creation_input_tokens=find("cache_creation_input_tokens"),
            )
            components = [
                counts[key]
                for key in (
                    "uncached_input_tokens",
                    "cache_read_input_tokens",
                    "cache_creation_input_tokens",
                )
            ]
            # Anthropic cache tokens are additional input; OpenAI cache tokens are a subset.
            counts["input_tokens"] = (
                sum(components) if all(value is not None for value in components) else None
            )
        else:
            input_details = (
                "prompt_tokens_details" if protocol == "chat" else "input_tokens_details"
            )
            output_details = (
                "completion_tokens_details" if protocol == "chat" else "output_tokens_details"
            )
            counts["cache_read_input_tokens"] = find(input_details, "cached_tokens")
            counts["reasoning_tokens"] = find(output_details, "reasoning_tokens")
        total = find("total_tokens")
        if total is None and counts["input_tokens"] is not None and outputs is not None:
            total = counts["input_tokens"] + outputs
        counts["total_tokens"] = total
        usage = {"usage_requests": 1}
        for name, value in counts.items():
            if value is not None:
                usage[name] = value
                usage[f"{name}_requests"] = 1
        return usage

    @staticmethod
    def _check_completion(data, protocol, usage):
        if protocol == "responses":
            reason = data.get("status", "")
            incomplete = reason in {"incomplete", "failed", "cancelled", "queued", "in_progress"}
            if reason == "incomplete":
                details = data.get("incomplete_details") or {}
                cause = details.get("reason") if isinstance(details, dict) else None
                if cause in {"max_output_tokens", "content_filter"}:
                    reason = cause
        elif protocol == "chat":
            reason = data["choices"][0].get("finish_reason", "")
            incomplete = reason in {"length", "content_filter"}
        else:
            reason = data.get("stop_reason", "")
            incomplete = reason in {"max_tokens", "pause_turn"}
        if incomplete:
            raise ProviderError(
                "Provider stopped before a complete model response",
                category="incomplete_response",
                code=f"{protocol}:{reason}",
                usage=usage,
            )

    @staticmethod
    def _responses_reply(data, usage=None):
        ModelClient._check_completion(data, "responses", usage)
        calls, texts = [], []
        for item in data.get("output", []):
            if item.get("type") == "function_call":
                calls.append(
                    ToolCall(
                        name=item["name"],
                        arguments=json.loads(item.get("arguments", "{}")),
                        call_id=item.get("call_id", item.get("id", "")),
                    )
                )
            for block in item.get("content", []):
                if block.get("type") in {"output_text", "text"}:
                    texts.append(block.get("text", ""))
        return ModelReply(
            text="".join(texts) or data.get("output_text", "") or "",
            calls=calls,
            usage=usage or {},
            raw=data.get("output", []),
            provider_model=data.get("model", ""),
        )

    @staticmethod
    def _chat_reply(data, usage=None):
        ModelClient._check_completion(data, "chat", usage)
        message = data["choices"][0]["message"]
        calls = [
            ToolCall(
                name=item["function"]["name"],
                arguments=json.loads(item["function"].get("arguments", "{}")),
                call_id=item["id"],
            )
            for item in message.get("tool_calls", [])
        ]
        return ModelReply(
            text=message.get("content") or "",
            calls=calls,
            usage=usage or {},
            raw=[message],
            provider_model=data.get("model", ""),
        )

    @staticmethod
    def _anthropic_reply(data, usage=None):
        ModelClient._check_completion(data, "anthropic", usage)
        text, calls = "", []
        for block in data.get("content", []):
            if block.get("type") == "text":
                text += block.get("text", "")
            elif block.get("type") == "tool_use":
                calls.append(
                    ToolCall(
                        name=block["name"], arguments=block.get("input", {}), call_id=block["id"]
                    )
                )
        return ModelReply(
            text=text,
            calls=calls,
            usage=usage or {},
            raw=[{"role": "assistant", "content": data.get("content", [])}],
            provider_model=data.get("model", ""),
        )
