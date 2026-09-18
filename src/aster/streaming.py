"""Bounded Responses SSE transport; deltas are observations, never executable calls.

Wire contract: https://developers.openai.com/api/docs/guides/streaming-responses
The completed event contains the authoritative response, parsed by ModelClient.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time

import httpx

from .evidence import Redactor
from .provider import ProviderError

MAX_EVENT_BYTES = 16 * 1024 * 1024
MAX_STREAM_BYTES = 64 * 1024 * 1024
MAX_PROGRESS_CHARS = 1024 * 1024
MAX_PROGRESS_EVENTS = 512
RETRYABLE_HTTP = {408, 409, 425, 429, 500, 502, 503, 504, 524}


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


async def complete_responses(model, payload, extra, on_event):
    settings = model.settings
    secret = settings.api_key.get_secret_value()
    if not secret and settings.auth != "none":
        raise ProviderError("ASTER_API_KEY is empty", category="configuration")
    headers = {"Content-Type": "application/json", "Accept": "text/event-stream", **extra}
    if settings.auth != "none":
        headers["Authorization"] = f"Bearer {secret}"
    model.last_usage = {}
    progress = None
    try:
        async with httpx.AsyncClient(timeout=settings.request_timeout) as client:
            for attempt in range(1, 4):
                progress = _Progress(on_event, [*model.redaction_secrets, secret], attempt)
                progress.emit("begin")
                retry_after = 0
                try:
                    async with asyncio.timeout(settings.request_timeout):
                        async with client.stream(
                            "POST",
                            settings.base_url.rstrip("/") + "/responses",
                            headers=headers,
                            json={**payload, "stream": True},
                        ) as response:
                            if not response.is_success:
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
                                        "text_delta"
                                        if kind == "response.output_text.delta"
                                        else "tool_delta",
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
                                if not isinstance(terminal, dict) or terminal.get(
                                    "status"
                                ) != kind.removeprefix("response."):
                                    raise ProviderError(
                                        "Invalid terminal response", category="invalid_response"
                                    )
                                model.last_usage = model._usage(terminal.get("usage"))
                                if kind == "response.completed" and (
                                    not isinstance(terminal.get("output"), list)
                                    or terminal.get("error")
                                ):
                                    raise ProviderError(
                                        "Invalid completed response", category="invalid_response"
                                    )
                                if kind == "response.completed":
                                    for output in terminal["output"]:
                                        if (
                                            not isinstance(output, dict)
                                            or output.get("status") not in {None, "completed"}
                                            or (
                                                output.get("type") == "function_call"
                                                and not isinstance(output.get("arguments"), str)
                                            )
                                        ):
                                            raise ProviderError(
                                                "Incomplete terminal output item",
                                                category="invalid_response",
                                            )
                                reply = model._responses_reply(terminal, model.last_usage)
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
                        usage=model.last_usage,
                    )
                except ProviderError as exc:
                    error = ProviderError(
                        **{
                            **exc.details,
                            "attempts": attempt,
                            "usage": exc.details["usage"] or model.last_usage,
                        }
                    )
                retry = error.details["category"] in {"transport", "transient_http"} and attempt < 3
                progress.emit("error", **error.details, retrying=retry, **progress.timing())
                if not retry:
                    raise error
                await asyncio.sleep(min(2, max(0.25 * 2 ** (attempt - 1), retry_after)))
    except asyncio.CancelledError:
        if progress is not None:
            progress.emit("error", category="cancelled", retrying=False, **progress.timing())
        raise
