"""Experimental operator-only Responses transport; not Aster's product streaming API.

Buffer until a complete terminal response, then reuse ModelClient's ordinary parser.
This website experiment fixes reasoning effort to low and does not retry a stream.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path

import httpx

from aster.providers.client import ModelClient
from aster.providers.errors import ProviderError
from aster.safety.redaction import Redactor


async def _sse_events(response):
    """Dispatch data only at blank-line boundaries; comments/empty events are ignored."""
    data, event = [], ""
    size = 0
    async for line in response.aiter_lines():
        if not line:
            if data:
                yield event, "\n".join(data)
            data, event, size = [], "", 0
            continue
        if line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        if value.startswith(" "):
            value = value[1:]
        if field == "data":
            size += len(value)
            if size > 16 * 1024 * 1024:
                raise ProviderError("SSE event exceeds 16 MiB", category="invalid_response")
            data.append(value)
        elif field == "event":
            event = value
    # An unterminated event at EOF is incomplete and must not authorize tools.


class ResponsesStreamingClient(ModelClient):
    """Experimental transport injected only by the website operator's launcher."""

    def __init__(self, settings, audit_path: Path):
        super().__init__(settings)
        self.audit_path = Path(audit_path)
        self.request_number = 0

    def _audit(self, record):
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        with self.audit_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record) + "\n")

    def _terminal_diagnostics(self, response):
        secret = self.settings.api_key.get_secret_value()
        redactor = Redactor([secret, self.settings.base_url])
        diagnostics = {}
        for field, keys in {
            "error": ("code", "type", "message", "param"),
            "incomplete_details": ("reason",),
        }.items():
            source = response.get(field)
            if not isinstance(source, dict):
                continue
            safe = {}
            for key in keys:
                if not isinstance(source.get(key), str):
                    continue
                text = redactor.text(source[key]).replace(secret, "[REDACTED]")
                text = re.sub(r"https?://\S+", "[provider URL]", text)
                text = re.sub(
                    r"(?i)\b(password|api[_ -]?key|authorization|secret|base_url)\s*[:=]\s*\S+",
                    r"\1=[REDACTED]", text,
                )
                safe[key] = text[:800]
            diagnostics[field] = safe
        return diagnostics

    async def _post(self, suffix: str, payload: dict, extra: dict, accounting, *, on_event=None) -> dict:
        if self.settings.protocol != "responses" or suffix != "/responses":
            raise ProviderError("Experimental transport requires Responses", category="configuration")
        secret = self.settings.api_key.get_secret_value()
        if not secret:
            raise ProviderError("ASTER_API_KEY is empty", category="configuration")
        self.request_number += 1
        number, started = self.request_number, time.monotonic()
        count, first_event, last_progress = 0, None, 0.0
        status, terminal, error_category = None, "", ""
        diagnostics = {}
        self._audit({"request": number, "event": "started", "reasoning_effort": "low"})
        headers = {
            "Authorization": f"Bearer {secret}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            **extra,
        }
        body = {**payload, "stream": True, "reasoning": {"effort": "low"}}

        def progress():
            print(f"stream request {number}: events={count}, elapsed={time.monotonic() - started:.2f}s", flush=True)

        try:
            async with asyncio.timeout(self.settings.request_timeout):
                async with httpx.AsyncClient(timeout=self.settings.request_timeout) as client:
                    async with client.stream(
                        "POST", self.settings.base_url.rstrip("/") + suffix,
                        headers=headers, json=body,
                    ) as response:
                        status = response.status_code
                        if not response.is_success:
                            retryable = status in {408, 409, 425, 429, 500, 502, 503, 504, 524}
                            raise ProviderError(
                                "Experimental streaming request failed",
                                category="transient_http" if retryable else "request_rejected",
                                status=status,
                            )
                        async for event, data in _sse_events(response):
                            count += 1
                            elapsed = time.monotonic() - started
                            if first_event is None:
                                first_event = round(elapsed, 3)
                                self._audit({"request": number, "event": "first_event", "elapsed_seconds": first_event})
                            if count == 1 or elapsed - last_progress >= 10:
                                progress()
                                last_progress = elapsed
                            if data == "[DONE]":
                                continue
                            try:
                                item = json.loads(data)
                            except ValueError as exc:
                                raise ProviderError("Invalid SSE JSON", category="invalid_response") from exc
                            if not isinstance(item, dict):
                                raise ProviderError("Invalid SSE event", category="invalid_response")
                            kind = item.get("type", event)
                            if not isinstance(kind, str):
                                raise ProviderError("Invalid SSE event type", category="invalid_response")
                            if kind == "error":
                                diagnostics = self._terminal_diagnostics({"error": item})
                                raise ProviderError("Provider reported a streaming error", category="provider_error")
                            if kind not in {"response.completed", "response.incomplete", "response.failed"}:
                                continue
                            complete = item.get("response")
                            expected = kind.removeprefix("response.")
                            if not isinstance(complete, dict) or complete.get("status") != expected:
                                raise ProviderError("Invalid terminal response", category="invalid_response")
                            if expected == "completed" and not isinstance(complete.get("output"), list):
                                raise ProviderError("Completed response has no output array", category="invalid_response")
                            terminal = expected
                            if expected != "completed":
                                diagnostics = self._terminal_diagnostics(complete)
                            accounting.record(complete)
                            # complete() delegates incomplete/failed handling and tool parsing
                            # to providers.codec.responses_reply; deltas never become tool calls.
                            return complete
                        raise ProviderError("SSE ended without a terminal response", category="incomplete_response")
        except (TimeoutError, httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
            error_category = "transport"
            raise ProviderError(type(exc).__name__, category="transport") from exc
        except ProviderError as exc:
            error_category = exc.details["category"]
            raise
        except asyncio.CancelledError:
            error_category = "cancelled"
            raise
        finally:
            self._audit({
                "request": number, "event": "finished", "status": status,
                "first_event_seconds": first_event, "duration_seconds": round(time.monotonic() - started, 3),
                "event_count": count, "terminal": terminal, "error_category": error_category,
                **diagnostics,
            })
            progress()
