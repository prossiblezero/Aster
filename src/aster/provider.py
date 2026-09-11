"""OpenAI-compatible Responses/Chat and Anthropic-compatible message clients."""

from __future__ import annotations

import json
from typing import Any

import httpx

from .config import Settings
from .schema import ModelReply, ToolCall


class ProviderError(RuntimeError):
    pass


class ModelClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.last_usage: dict[str, int] = {}

    async def complete(self, messages: list[dict[str, Any]], tools: list[dict]) -> ModelReply:
        if self.settings.protocol == "anthropic":
            payload = {"model": self.settings.model, "max_tokens": self.settings.output_tokens,
                       "messages": [{"role": "user", "content": self._text(messages)}],
                       "tools": [self._anthropic_tool(t) for t in tools]}
            headers = {"x-api-key": self.settings.api_key.get_secret_value(),
                       "anthropic-version": "2023-06-01"}
            data = await self._post("/messages", payload, headers)
            return self._anthropic_reply(data)
        if self.settings.protocol == "chat":
            payload = {"model": self.settings.model, "messages": messages,
                       "max_tokens": self.settings.output_tokens, "tools": tools}
            data = await self._post("/chat/completions", payload, {})
            return self._chat_reply(data)
        payload = {"model": self.settings.model, "input": messages, "tools": tools,
                   "max_output_tokens": self.settings.output_tokens, "store": False}
        data = await self._post("/responses", payload, {})
        return self._responses_reply(data)

    async def _post(self, suffix: str, payload: dict, extra: dict) -> dict:
        if not self.settings.api_key.get_secret_value():
            raise ProviderError("ASTER_API_KEY is empty")
        headers = {"Authorization": f"Bearer {self.settings.api_key.get_secret_value()}",
                   "Content-Type": "application/json", **extra}
        url = self.settings.base_url.rstrip("/") + suffix
        async with httpx.AsyncClient(timeout=self.settings.request_timeout) as client:
            try:
                response = await client.post(url, headers=headers, json=payload)
                response.raise_for_status()
                data = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                raise ProviderError(f"provider request failed: {type(exc).__name__}: {exc}") from exc
        if data.get("error"):
            raise ProviderError(str(data["error"]))
        self.last_usage = self._usage(data.get("usage", {}))
        return data

    @staticmethod
    def _usage(raw: dict) -> dict[str, int]:
        def find(key):
            value = raw.get(key, 0)
            return int(value) if isinstance(value, (int, float)) else 0
        return {"input_tokens": find("input_tokens") or find("prompt_tokens"),
                "output_tokens": find("output_tokens") or find("completion_tokens"),
                "total_tokens": find("total_tokens")}

    @staticmethod
    def _text(messages):
        return "\n\n".join(str(m.get("content", "")) for m in messages)

    @staticmethod
    def _responses_reply(data):
        calls, text = [], data.get("output_text", "") or ""
        for item in data.get("output", []):
            if item.get("type") == "function_call":
                try:
                    args = json.loads(item.get("arguments", "{}"))
                except json.JSONDecodeError:
                    args = {}
                calls.append(ToolCall(name=item["name"], arguments=args,
                                      call_id=item.get("call_id", item.get("id", ""))))
            for block in item.get("content", []):
                if block.get("type") in {"output_text", "text"}:
                    text += block.get("text", "")
        return ModelReply(text=text, calls=calls, raw=data.get("output", []),
                          provider_model=data.get("model", ""))

    @staticmethod
    def _chat_reply(data):
        message = data.get("choices", [{}])[0].get("message", {})
        calls = []
        for item in message.get("tool_calls", []):
            try:
                args = json.loads(item.get("function", {}).get("arguments", "{}"))
            except json.JSONDecodeError:
                args = {}
            calls.append(ToolCall(name=item["function"]["name"], arguments=args,
                                  call_id=item.get("id", "")))
        return ModelReply(text=message.get("content") or "", calls=calls,
                          raw=[message], provider_model=data.get("model", ""))

    @staticmethod
    def _anthropic_tool(tool):
        return {"name": tool["name"], "description": tool["description"],
                "input_schema": tool["parameters"]}

    @staticmethod
    def _anthropic_reply(data):
        text, calls = "", []
        for block in data.get("content", []):
            if block.get("type") == "text":
                text += block.get("text", "")
            elif block.get("type") == "tool_use":
                calls.append(ToolCall(name=block["name"], arguments=block.get("input", {}),
                                      call_id=block.get("id", "")))
        return ModelReply(text=text, calls=calls, raw=data.get("content", []),
                          provider_model=data.get("model", ""))
