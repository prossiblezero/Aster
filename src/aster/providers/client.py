"""Model request orchestration and nonstreaming HTTP transport."""
from __future__ import annotations

import asyncio
import re
import time
from typing import Any

import httpx

from ..config import Settings
from ..resilience import CircuitBreaker, CircuitOpen
from ..safety.redaction import Redactor
from ..schema import ModelReply
from .codec import parse_reply, request_payload
from .errors import RETRYABLE_HTTP, ProviderError, retry_delay, retry_request, transient_error_code
from .streaming import complete_responses
from .usage import RequestAccounting


class ModelClient:
    def __init__(self, settings: Settings, *, max_attempts: int = 3):
        self.settings = settings
        self.max_attempts = max(1, min(3, max_attempts))
        self.last_usage: dict[str, int] = {}
        self.last_diagnostics: dict[str, Any] = {}
        self.redaction_secrets = [settings.api_key.get_secret_value()]
        self.circuit = CircuitBreaker(settings.circuit_failures, settings.circuit_cooldown)

    async def complete(
        self, messages: list[dict[str, Any]], tools: list[dict], *, on_event=None
    ) -> ModelReply:
        try:
            with self.circuit.guard(lambda exc: isinstance(exc, ProviderError) and
                                    exc.details["category"] in {"transport", "transient_http", "transient_provider"}):
                return await self._complete(messages, tools, on_event=on_event)
        except CircuitOpen as exc:
            self.last_usage, self.last_diagnostics = {}, {"retry_after": exc.retry_after}
            raise ProviderError(str(exc), category="circuit_open", attempts=0,
                                diagnostics=self.last_diagnostics) from None

    async def _complete(
        self, messages: list[dict[str, Any]], tools: list[dict], *, on_event=None
    ) -> ModelReply:
        accounting = RequestAccounting(self.settings, self.redaction_secrets)
        try:
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
                return await complete_responses(
                    self.settings, payload, headers, on_event,
                    parse_reply=lambda data, usage: parse_reply(data, usage, protocol),
                    accounting=accounting,
                    max_attempts=self.max_attempts,
                )
            data = await self._post(suffix, payload, headers, accounting, on_event=on_event)
            reply = parse_reply(data, accounting.usage, protocol)
            reply.diagnostics = accounting.diagnostics
            return reply
        finally:
            self.last_usage = accounting.usage
            self.last_diagnostics = accounting.diagnostics

    async def _post(self, suffix: str, payload: dict, extra: dict, accounting, *, on_event=None) -> dict:
        secret = self.settings.api_key.get_secret_value()
        if not secret and self.settings.auth != "none":
            raise ProviderError("ASTER_API_KEY is empty", category="configuration")
        headers = {"Content-Type": "application/json", **extra}
        if self.settings.auth != "none":
            headers["Authorization"] = f"Bearer {secret}"
        else:
            headers.pop("x-api-key", None)
        url = self.settings.base_url.rstrip("/") + suffix
        redactor = Redactor([*self.redaction_secrets, secret])
        empty_retries = 0
        async with httpx.AsyncClient(timeout=self.settings.request_timeout) as client:
            for attempt in range(1, self.max_attempts + 1):
                started = time.monotonic()
                data, response, usage = None, None, {}
                delay = retry_delay(attempt)
                try:
                    response = await client.post(url, headers=headers, json=payload)
                    try:
                        data = response.json()
                    except ValueError:
                        data = None
                    if isinstance(data, dict) and (response.is_success or "usage" in data):
                        usage = accounting.record(data)
                    if (
                        response.is_success
                        and isinstance(data, dict)
                        and data
                        and (not data.get("error") or (
                            self.settings.protocol == "responses"
                            and data.get("status") in ("failed", "incomplete", "cancelled")
                        ))
                    ):
                        parse_reply(data, usage, self.settings.protocol)  # Validate the entire batch before publication.
                        accounting.diagnose(
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
                    retryable = response.status_code in RETRYABLE_HTTP
                    transient = (
                        response.is_success and data.get("status") in (None, "")
                        and transient_error_code(raw)
                    ) if isinstance(data, dict) else False
                    raise ProviderError(
                        detail[:800],
                        category="transient_http"
                        if retryable
                        else "transient_provider" if transient
                        else ("invalid_response" if response.is_success else "request_rejected"),
                        status=response.status_code,
                        code=code,
                    )
                except asyncio.CancelledError:
                    accounting.diagnose(
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
                diagnostic = accounting.diagnose(
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
                        "usage": accounting.usage,
                        "diagnostics": diagnostic,
                    }
                )
                category = error.details["category"]
                retry, empty_retries = retry_request(category, attempt, empty_retries)
                if not retry or attempt >= self.max_attempts:
                    raise error
                if response is not None:
                    try:
                        delay = retry_delay(attempt, float(response.headers.get("retry-after", "0")))
                    except ValueError:
                        pass
                await asyncio.sleep(delay)
        raise error  # pragma: no cover
