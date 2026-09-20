"""Per-request accounting, numeric normalization and bounded redacted diagnostics."""
from __future__ import annotations

import hashlib
import logging
import re
import time
from copy import deepcopy
from itertools import islice

import httpx

from ..safety.redaction import Redactor

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


def normalize_usage(raw: dict, protocol: str = "responses") -> dict[str, int]:
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



class RequestAccounting:
    def __init__(self, settings, secrets):
        self.settings = settings
        self.redaction_secrets = tuple(secrets)
        self.usage = {}
        self.diagnostics = {}

    def record(self, data):
        usage = normalize_usage(data.get("usage"), self.settings.protocol)
        for key, value in usage.items():
            self.usage[key] = self.usage.get(key, 0) + value
        return usage


    def diagnose(self, payload, data, response, attempt, started, category, usage, callback):
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
        self.diagnostics = diagnostic
        if callback is not None:
            try:
                callback(deepcopy({"type": "diagnostic", **diagnostic}))
            except Exception as exc:
                logging.getLogger(__name__).warning(
                    "Provider observer failed: %s", type(exc).__name__
                )
        return diagnostic
