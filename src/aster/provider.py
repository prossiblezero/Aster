"""Small protocol adapters and bounded retries around one model request."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from copy import deepcopy
from itertools import islice
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
        diagnostics=None,
    ):
        self.details = {
            "category": category,
            "status": status,
            "code": code,
            "message": message,
            "attempts": attempts,
            "usage": dict(usage or {}),
            "diagnostics": dict(diagnostics or {}),
        }
        super().__init__(json.dumps(self.details, ensure_ascii=False))


_USAGE_FIELDS = {
    "input_tokens",
    "output_tokens",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "cached_tokens",
    "cache_write_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
    "reasoning_tokens",
    "audio_tokens",
    "accepted_prediction_tokens",
    "rejected_prediction_tokens",
    "uncached_tokens",
    "ephemeral_5m_input_tokens",
    "ephemeral_1h_input_tokens",
}


def _numeric_usage(raw):
    """Keep known counters, never arbitrary strings or provider-supplied mapping keys."""

    def counts(value):
        if not isinstance(value, dict):
            return {}
        return {
            key: value[key]
            for key in sorted(_USAGE_FIELDS)
            if type(value.get(key)) is int and 0 <= value[key] <= 2**63 - 1
        }

    result = counts(raw)
    if not isinstance(raw, dict):
        return result
    for key in (
        "input_tokens_details",
        "output_tokens_details",
        "prompt_tokens_details",
        "completion_tokens_details",
        "cache_creation",
    ):
        if isinstance(raw.get(key), dict):
            result[key] = counts(raw[key])
    attribution = raw.get("attribution")
    if isinstance(attribution, dict):
        result["attribution"] = attr = {}
        items = attribution.get("items")
        if isinstance(items, dict):
            attr["items_count"] = len(items)
            # IDs and content are deliberately omitted; top-level item counters suffice.
            attr["items"] = [counts(value) for value in islice(items.values(), 128)]
        fields = attribution.get("request_fields")
        if isinstance(fields, dict):
            attr["request_fields_count"] = len(fields)
            attr["request_fields"] = {
                key: counts(fields[key])
                for key in ("tools", "instructions", "messages", "input", "system")
                if key in fields
            }
    return result


def _usage_warnings(raw, usage):
    flags = []
    for left, right in (("input_tokens", "prompt_tokens"), ("output_tokens", "completion_tokens")):
        if left in raw and right in raw and raw[left] != raw[right]:
            flags.append(left + "_aliases_disagree")
    if all(key in usage for key in ("input_tokens", "output_tokens", "total_tokens")):
        if usage["total_tokens"] != usage["input_tokens"] + usage["output_tokens"]:
            flags.append("total_tokens_disagree")
    for part, total in (
        ("cache_read_input_tokens", "input_tokens"),
        ("reasoning_tokens", "output_tokens"),
    ):
        if part in usage and total in usage and usage[part] > usage[total]:
            flags.append(part + "_exceeds_" + total)
    attr = raw.get("attribution", {})
    items, fields = attr.get("items", []), attr.get("request_fields", {})
    if (
        items
        and len(items) == attr.get("items_count")
        and len(fields) == attr.get("request_fields_count")
        and all("input_tokens" in part for part in [*items, *fields.values()])
        and "input_tokens" in usage
    ):
        total = sum(part["input_tokens"] for part in [*items, *fields.values()])
        if total != usage["input_tokens"]:
            flags.append("input_attribution_disagrees")
    return flags


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


class ModelClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.last_usage: dict[str, int] = {}
        self.last_diagnostics: dict[str, Any] = {}
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
        data = await self._post(suffix, payload, headers, on_event=on_event)
        reply = self._parse(data, self.last_usage)
        reply.diagnostics = self.last_diagnostics
        return reply

    def _parse(self, data, usage):
        try:
            if not isinstance(data, dict):
                raise ValueError("response must be an object")
            return {
                "responses": self._responses_reply,
                "chat": self._chat_reply,
                "anthropic": self._anthropic_reply,
            }[self.settings.protocol](data, usage)
        except (ValueError, KeyError, TypeError, IndexError, AttributeError) as exc:
            raise ProviderError(
                "Malformed model response or tool arguments",
                category="invalid_response",
                usage=usage,
            ) from exc

    def _record_usage(self, data):
        usage = self._usage(data.get("usage"), self.settings.protocol)
        for key, value in usage.items():
            self.last_usage[key] = self.last_usage.get(key, 0) + value
        return usage

    def _diagnostic(self, payload, data, response, attempt, started, category, usage, callback):
        """Only bounded metadata and allowlisted numeric usage leave this boundary."""
        redactor = Redactor(self.redaction_secrets)

        def label(value, limit=160):
            if not isinstance(value, str):
                return ""
            value = redactor.text(value)
            value = re.sub(r"https?://\S+", "[provider URL]", value)
            value = re.sub(
                r"(?i)\b(password|api[_ -]?key|authorization|secret)\s*[:=]\s*\S+",
                r"\1=[REDACTED]",
                value,
            )
            return value[:limit]

        data = data if isinstance(data, dict) else {}
        raw = _numeric_usage(data.get("usage"))
        returned_model = data.get("model")
        flags = _usage_warnings(raw, usage)
        if isinstance(returned_model, str) and returned_model != self.settings.model:
            flags.append("response_model_mismatch")
        structure = []
        terminal_status = data.get("status", data.get("stop_reason", ""))
        output = data.get("output", data.get("content", []))
        if self.settings.protocol == "chat":
            choices = data.get("choices")
            if isinstance(choices, list) and choices and isinstance(choices[0], dict):
                terminal_status = choices[0].get("finish_reason", "")
            output = (
                [c.get("message") for c in choices[:32] if isinstance(c, dict)]
                if isinstance(choices, list)
                else []
            )
        if isinstance(output, list):
            for item in output[:32]:
                if not isinstance(item, dict):
                    structure.append({"type": "malformed"})
                    continue
                row = {"type": label(item.get("type", item.get("role", "unknown")), 80)}
                for key in (
                    "text",
                    "arguments",
                    "thinking",
                    "encrypted_content",
                    "content",
                    "reasoning_content",
                    "reasoning",
                ):
                    if isinstance(item.get(key), str):
                        row[key + "_length"] = len(item[key])
                tool_calls = item.get("tool_calls")
                if isinstance(tool_calls, list):
                    row["tool_call_count"] = len(tool_calls)
                    row["tool_calls"] = [
                        {
                            "type": label(call.get("type"), 80),
                            "arguments_length": len(call["function"]["arguments"])
                            if isinstance(call.get("function"), dict)
                            and isinstance(call["function"].get("arguments"), str)
                            else 0,
                        }
                        for call in tool_calls[:32]
                        if isinstance(call, dict)
                    ]
                if isinstance(item.get("input"), dict):
                    row["argument_keys_count"] = len(item["input"])
                content = item.get("content")
                if isinstance(content, list):
                    row["content_count"] = len(content)
                    row["content"] = [
                        {
                            "type": label(block.get("type"), 80),
                            "text_length": len(block["text"])
                            if isinstance(block.get("text"), str)
                            else 0,
                        }
                        for block in content[:32]
                        if isinstance(block, dict)
                    ]
                structure.append(row)
        diagnostic = {
            "requested_model": label(self.settings.model),
            "response_model": label(returned_model),
            "protocol": self.settings.protocol,
            "response_id": label(data.get("id")),
            "request_ids": {
                key: label(response.headers[key])
                for key in ("x-request-id", "request-id", "anthropic-request-id")
                if response is not None and key in response.headers
            },
            # httpx uses this same JSON encoder for the actual json= request body.
            "payload_sha256": hashlib.sha256(
                httpx.Request("POST", "https://unused", json=payload).content
            ).hexdigest(),
            "attempt": attempt,
            "elapsed_seconds": round(time.monotonic() - started, 6),
            "classification": category,
            "terminal_status": label(terminal_status, 80),
            "output_count": len(output) if isinstance(output, list) else None,
            "output_structure": structure,
            "output_text_length": len(data["output_text"])
            if isinstance(data.get("output_text"), str)
            else 0,
            "raw_usage": raw,
            "usage": dict(usage),
            "usage_observed": isinstance(data.get("usage"), dict),
            "flags": flags,
        }
        self.last_diagnostics = diagnostic
        if callback is not None:
            try:
                callback(deepcopy({"type": "diagnostic", **diagnostic}))
            except Exception as exc:
                logging.getLogger(__name__).warning(
                    "Provider observer failed: %s", type(exc).__name__
                )
        return diagnostic

    async def _post(self, suffix: str, payload: dict, extra: dict, *, on_event=None) -> dict:
        secret = self.settings.api_key.get_secret_value()
        if not secret and self.settings.auth != "none":
            raise ProviderError("ASTER_API_KEY is empty", category="configuration")
        self.last_usage, self.last_diagnostics = {}, {}
        headers = {"Content-Type": "application/json", **extra}
        if self.settings.auth != "none":
            headers["Authorization"] = f"Bearer {secret}"
        else:
            headers.pop("x-api-key", None)
        url = self.settings.base_url.rstrip("/") + suffix
        redactor = Redactor([*self.redaction_secrets, secret])
        empty_retries = 0
        async with httpx.AsyncClient(timeout=self.settings.request_timeout) as client:
            for attempt in range(1, 4):
                started = time.monotonic()
                data, response, usage = None, None, {}
                delay = 0.25 * 2 ** (attempt - 1)
                try:
                    response = await client.post(url, headers=headers, json=payload)
                    try:
                        data = response.json()
                    except ValueError:
                        data = None
                    if isinstance(data, dict) and (response.is_success or "usage" in data):
                        usage = self._record_usage(data)
                    if (
                        response.is_success
                        and isinstance(data, dict)
                        and data
                        and not data.get("error")
                    ):
                        self._parse(data, usage)  # Validate the entire batch before publication.
                        self._diagnostic(
                            payload, data, response, attempt, started, "success", usage, on_event
                        )
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
                    code = code if re.fullmatch(r"[a-zA-Z0-9_.:-]*", code) else ""
                    retryable = response.status_code in {
                        408,
                        409,
                        425,
                        429,
                        500,
                        502,
                        503,
                        504,
                        524,
                    }
                    raise ProviderError(
                        detail[:800],
                        category="transient_http"
                        if retryable
                        else ("invalid_response" if response.is_success else "request_rejected"),
                        status=response.status_code,
                        code=code,
                    )
                except asyncio.CancelledError:
                    self._diagnostic(
                        payload, data, response, attempt, started, "cancelled", usage, on_event
                    )
                    raise
                except (
                    httpx.TimeoutException,
                    httpx.NetworkError,
                    httpx.RemoteProtocolError,
                ) as exc:
                    error = ProviderError(type(exc).__name__, category="transport")
                except ProviderError as exc:
                    error = exc
                diagnostic = self._diagnostic(
                    payload,
                    data,
                    response,
                    attempt,
                    started,
                    error.details["category"],
                    usage,
                    on_event,
                )
                error = ProviderError(
                    **{
                        **error.details,
                        "attempts": attempt,
                        "usage": self.last_usage,
                        "diagnostics": diagnostic,
                    }
                )
                category = error.details["category"]
                retry = category in {"transport", "transient_http"}
                if category == "empty_response" and empty_retries == 0:
                    empty_retries += 1
                    retry = True
                if not retry or attempt == 3:
                    raise error
                if response is not None:
                    try:
                        delay = min(2, max(delay, float(response.headers.get("retry-after", "0"))))
                    except ValueError:
                        pass
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
            return value if type(value) is int and 0 <= value <= 2**63 - 1 else None

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

    @staticmethod
    def _responses_reply(data, usage=None):
        ModelClient._check_completion(data, "responses", usage)
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

    @staticmethod
    def _chat_reply(data, usage=None):
        ModelClient._check_completion(data, "chat", usage)
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

    @staticmethod
    def _anthropic_reply(data, usage=None):
        ModelClient._check_completion(data, "anthropic", usage)
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
