"""Bounded Responses SSE transport; deltas are observations, never executable calls.

Wire contract: https://developers.openai.com/api/docs/guides/streaming-responses
The completed event contains the authoritative response, parsed by the shared codec.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time

import httpx

from ..evidence import Redactor
from .errors import RETRYABLE_HTTP, ProviderError, retry_delay, retry_request

MAX_EVENT_BYTES = 16 * 1024 * 1024
MAX_STREAM_BYTES = 64 * 1024 * 1024
MAX_PROGRESS_CHARS = 1024 * 1024
MAX_PROGRESS_EVENTS = 512


async def _lines(response):
    """Bound undecoded lines, including ones without a newline, before parsing SSE."""
    buffer, total = b"", 0
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total > MAX_STREAM_BYTES:
            raise ProviderError("SSE stream exceeds capacity", category="invalid_response")
        buffer += chunk
        while match := re.search(rb"\r\n|\r|\n", buffer):
            if buffer[match.start() :] == b"\r":
                break  # A CRLF pair may straddle HTTP chunks.
            if match.start() > MAX_EVENT_BYTES:
                raise ProviderError("SSE line exceeds capacity", category="invalid_response")
            yield buffer[: match.start()].decode("utf-8")
            buffer = buffer[match.end() :]
        if len(buffer) > MAX_EVENT_BYTES:
            raise ProviderError("SSE line exceeds capacity", category="invalid_response")
    if buffer.endswith(b"\r"):
        yield buffer[:-1].decode("utf-8")


async def _sse_events(response):
    data, event, size = [], "", 0
    async for line in _lines(response):
        if not line:
            if data:
                yield event, "\n".join(data)
            data, event, size = [], "", 0
            continue
        size += len(line.encode("utf-8"))
        if size > MAX_EVENT_BYTES:
            raise ProviderError("SSE event exceeds capacity", category="invalid_response")
        if line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        if value.startswith(" "):
            value = value[1:]
        if field == "data":
            data.append(value)
        elif field == "event":
            event = value
    # EOF does not dispatch an event that lacks its blank-line terminator.


class _Progress:
    """A bounded, redacted observer; its failures cannot alter authoritative results."""

    def __init__(self, callback, secrets, attempt):
        self.callback, self.attempt = callback, attempt
        self.redactor = Redactor(secrets)
        self.secrets = sorted({value for value in secrets if value}, key=len, reverse=True)
        self.started = time.monotonic()
        self.first_event = self.first_text = None
        self.pending = {}
        self.characters = 0
        self.delta_events = 0
        self.truncated = False

    def emit(self, kind, **fields):
        if self.callback is None:
            return
        value = self.redactor.value(
            {
                "type": kind,
                "attempt": self.attempt,
                "elapsed_seconds": round(time.monotonic() - self.started, 6),
                **fields,
            }
        )
        try:
            self.callback(value)
        except Exception as exc:
            logging.getLogger(__name__).warning("Stream observer failed: %s", type(exc).__name__)

    def delta(self, kind, text, output_index, item_id, *, final=False):
        key = kind, output_index, item_id
        text = self.pending.pop(key, "") + text
        for secret in self.secrets:
            text = text.replace(secret, "[REDACTED]")
        # Retain only suffixes that may grow into a credential in the next delta.
        keep = (
            0
            if final
            else max(
                (
                    length
                    for secret in self.secrets
                    for length in range(1, min(len(secret), len(text) + 1))
                    if text.endswith(secret[:length])
                ),
                default=0,
            )
        )
        token = re.search(
            r"(?i)\b(?:sk-[a-z0-9_-]*|bearer(?:\s+[a-z0-9._-]*)?|s|sk|b|be|bea|bear|beare)$",
            text,
        )
        if token and not final:
            keep = max(keep, len(text) - token.start())
        if keep:
            text, self.pending[key] = text[:-keep], text[-keep:]
        text = self.redactor.text(text)
        if kind == "text_delta" and text.strip() and self.first_text is None:
            self.first_text = time.monotonic() - self.started
        remaining = max(0, MAX_PROGRESS_CHARS - self.characters)
        self.characters += len(text)
        for offset in range(0, min(len(text), remaining), 4096):
            if self.delta_events >= MAX_PROGRESS_EVENTS:
                self.truncated = True
                break
            self.delta_events += 1
            self.emit(
                kind,
                text=text[offset : min(offset + 4096, remaining)],
                output_index=output_index,
                item_id=item_id,
                truncated=self.characters > MAX_PROGRESS_CHARS,
            )

    def flush(self):
        for (kind, index, item_id), text in list(self.pending.items()):
            self.pending.pop((kind, index, item_id))
            self.delta(kind, text, index, item_id, final=True)

    def timing(self):
        return {
            "first_event_seconds": self.first_event,
            "first_text_seconds": self.first_text,
            "progress_truncated": self.truncated or self.characters > MAX_PROGRESS_CHARS,
        }


async def complete_responses(settings, payload, extra, on_event, *, parse_reply, accounting):
    secret = settings.api_key.get_secret_value()
    if not secret and settings.auth != "none":
        raise ProviderError("ASTER_API_KEY is empty", category="configuration")
    headers = {"Content-Type": "application/json", "Accept": "text/event-stream", **extra}
    if settings.auth != "none":
        headers["Authorization"] = f"Bearer {secret}"
    payload = {**payload, "stream": True}
    empty_retries = 0
    progress = None
    diagnosed = False
    try:
        async with httpx.AsyncClient(timeout=settings.request_timeout) as client:
            for attempt in range(1, 4):
                progress = _Progress(on_event, [*accounting.redaction_secrets, secret], attempt)
                progress.emit("begin")
                retry_after = 0
                terminal, response, usage = None, None, {}
                diagnosed = False
                try:
                    async with asyncio.timeout(settings.request_timeout):
                        async with client.stream(
                            "POST",
                            settings.base_url.rstrip("/") + "/responses",
                            headers=headers,
                            json=payload,
                        ) as response:
                            if not response.is_success:
                                body = bytearray()
                                async for chunk in response.aiter_bytes():
                                    body.extend(chunk)
                                    if len(body) > MAX_EVENT_BYTES:
                                        raise ProviderError(
                                            "HTTP error exceeds capacity",
                                            category="invalid_response",
                                        )
                                try:
                                    terminal = json.loads(body)
                                except ValueError:
                                    terminal = None
                                if isinstance(terminal, dict) and "usage" in terminal:
                                    usage = accounting.record(terminal)
                                try:
                                    retry_after = float(response.headers.get("retry-after", "0"))
                                except ValueError:
                                    pass
                                raise ProviderError(
                                    "Streaming request failed",
                                    category="transient_http"
                                    if response.status_code in RETRYABLE_HTTP
                                    else "request_rejected",
                                    status=response.status_code,
                                )
                            async for event, raw in _sse_events(response):
                                if raw == "[DONE]":
                                    continue
                                item = json.loads(raw)
                                if not isinstance(item, dict) or not isinstance(
                                    item.get("type", event), str
                                ):
                                    raise ProviderError(
                                        "Invalid SSE event", category="invalid_response"
                                    )
                                if progress.first_event is None:
                                    progress.first_event = time.monotonic() - progress.started
                                kind = item.get("type", event)
                                if kind == "error":
                                    raise ProviderError(
                                        "Provider reported a streaming error",
                                        category="provider_error",
                                    )
                                if kind in {
                                    "response.output_text.delta",
                                    "response.function_call_arguments.delta",
                                    "response.reasoning_summary_text.delta",
                                }:
                                    delta, index, item_id = (
                                        item.get("delta"),
                                        item.get("output_index"),
                                        item.get("item_id"),
                                    )
                                    if (
                                        not isinstance(delta, str)
                                        or type(index) is not int
                                        or index < 0
                                        or not isinstance(item_id, str)
                                        or len(item_id) > 160
                                    ):
                                        raise ProviderError(
                                            "Invalid SSE delta", category="invalid_response"
                                        )
                                    progress.delta(
                                        {
                                            "response.output_text.delta": "text_delta",
                                            "response.function_call_arguments.delta": "tool_delta",
                                            "response.reasoning_summary_text.delta": "reasoning_delta",
                                        }[kind],
                                        delta,
                                        index,
                                        item_id,
                                    )
                                if kind not in {
                                    "response.completed",
                                    "response.incomplete",
                                    "response.failed",
                                }:
                                    continue
                                terminal = item.get("response")
                                if isinstance(terminal, dict):
                                    usage = accounting.record(terminal)
                                if not isinstance(terminal, dict) or terminal.get(
                                    "status"
                                ) != kind.removeprefix("response."):
                                    raise ProviderError(
                                        "Invalid terminal response", category="invalid_response"
                                    )
                                if kind == "response.completed" and (
                                    not isinstance(terminal.get("output"), list)
                                    or terminal.get("error")
                                ):
                                    raise ProviderError(
                                        "Invalid completed response", category="invalid_response"
                                    )
                                reply = parse_reply(terminal, accounting.usage)
                                reply.diagnostics = accounting.diagnose(
                                    payload,
                                    terminal,
                                    response,
                                    attempt,
                                    progress.started,
                                    "success",
                                    usage,
                                    on_event,
                                )
                                diagnosed = True
                                progress.flush()
                                progress.emit("completed", usage=reply.usage, **progress.timing())
                                # The first complete terminal closes the stream. A duplicate
                                # terminal can neither append output nor charge usage twice.
                                return reply
                            raise ProviderError(
                                "SSE ended without a terminal response",
                                category="incomplete_response",
                            )
                except (
                    TimeoutError,
                    httpx.TimeoutException,
                    httpx.NetworkError,
                    httpx.RemoteProtocolError,
                ) as exc:
                    error = ProviderError(
                        type(exc).__name__, category="transport", attempts=attempt
                    )
                except (ValueError, KeyError, TypeError, IndexError, AttributeError):
                    error = ProviderError(
                        "Malformed stream or tool arguments",
                        category="invalid_response",
                        attempts=attempt,
                        usage=accounting.usage,
                    )
                except ProviderError as exc:
                    error = ProviderError(
                        **{
                            **exc.details,
                            "attempts": attempt,
                            "usage": exc.details["usage"] or accounting.usage,
                        }
                    )
                diagnostic = accounting.diagnose(
                    payload,
                    terminal,
                    response,
                    attempt,
                    progress.started,
                    error.details["category"],
                    usage,
                    on_event,
                )
                diagnosed = True
                error = ProviderError(
                    **{
                        **error.details,
                        "usage": accounting.usage,
                        "attempts": attempt,
                        "diagnostics": diagnostic,
                    }
                )
                category = error.details["category"]
                retry, empty_retries = retry_request(category, attempt, empty_retries)
                progress.emit("error", **error.details, retrying=retry, **progress.timing())
                if not retry:
                    raise error
                await asyncio.sleep(retry_delay(attempt, retry_after))
    except asyncio.CancelledError:
        if progress is not None:
            if not diagnosed:
                accounting.diagnose(
                    payload,
                    terminal,
                    response,
                    progress.attempt,
                    progress.started,
                    "cancelled",
                    usage,
                    on_event,
                )
            progress.emit("error", category="cancelled", retrying=False, **progress.timing())
        raise
