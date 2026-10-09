"""Safe provider failures and the shared bounded retry rules."""
import json


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



RETRYABLE_HTTP = {408, 409, 425, 429, 500, 502, 503, 504, 524}


def transient_error_code(error):
    """Only known service failures are retryable; error prose is not a contract."""
    code = error.get("code", error.get("type")) if isinstance(error, dict) else None
    return code if isinstance(code, str) and code in {
        "server_error", "overloaded_error", "rate_limit_exceeded", "rate_limit_error",
    } else ""


def retry_request(category, attempt, empty_retries):
    retry = category in {"transport", "transient_http", "transient_provider"}
    if category == "empty_response" and empty_retries == 0:
        empty_retries += 1
        retry = True
    return retry and attempt < 3, empty_retries


def retry_delay(attempt, retry_after=0):
    return min(2, max(0.25 * 2 ** (attempt - 1), retry_after))
