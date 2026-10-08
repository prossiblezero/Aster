"""Shared credential redaction, independent of storage and runtime."""

import re
from typing import Any


class Redactor:
    def __init__(self, secrets: list[str] = ()):
        self.secrets = sorted({x for x in secrets if len(x) >= 6}, key=len, reverse=True)

    def text(self, text: str) -> str:
        for secret in self.secrets:
            text = text.replace(secret, "[REDACTED]")
        return re.sub(r"(?i)\b(?:sk-[a-z0-9_-]{12,}|bearer\s+[a-z0-9._-]{12,})", "[REDACTED]", text)

    def value(self, value: Any, *, mask_fields: bool = True) -> Any:
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, dict):
            return {
                k: "[REDACTED]"
                if mask_fields and re.fullmatch(r"(?i)(api_key|password|secret|authorization)", k)
                else self.value(v, mask_fields=mask_fields)
                for k, v in value.items()
            }
        if isinstance(value, list):
            return [self.value(v, mask_fields=mask_fields) for v in value]
        return value
