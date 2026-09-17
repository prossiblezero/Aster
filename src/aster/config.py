"""Explicit configuration. Repository text can never change runtime permissions."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator

from .schema import Record

# Verified official limits; unknown model aliases deliberately use the small fallback.
# https://developers.openai.com/api/docs/models/gpt-5.6-luna (2026-09-16)
MODEL_CONTEXT_LIMITS = {
    "gpt-5.6-luna": {"window": 1_050_000, "input": 922_000, "output": 128_000},
}


class ExecRule(Record):
    prefix: list[str] = Field(min_length=1)
    decision: Literal["allow", "ask", "deny"]
    reason: str = Field(min_length=1)
    match: list[list[str]] = Field(default_factory=list)
    not_match: list[list[str]] = Field(default_factory=list)

    @model_validator(mode="after")
    def examples_match(self):
        for args in self.match:
            if args[: len(self.prefix)] != self.prefix:
                raise ValueError("rule match example does not match its prefix")
        for args in self.not_match:
            if args[: len(self.prefix)] == self.prefix:
                raise ValueError("rule not_match example matches its prefix")
        return self


class Settings(Record):
    protocol: Literal["responses", "chat", "anthropic"] = "responses"
    model: str = "gpt-5.6-luna"
    base_url: str = "https://api.openai.com/v1"
    api_key: SecretStr = Field(default_factory=lambda: SecretStr(""))
    request_timeout: float = Field(default=90, gt=0, le=300)
    context_tokens: int | None = Field(
        default=None,
        ge=1024,
        description="Total token budget including output reserve; omit for the model default.",
    )
    output_tokens: int | None = Field(default=None, ge=128)
    approval: Literal["ask", "auto", "never"] = "ask"
    sandbox: Literal["required", "best_effort", "off"] = "required"
    network: bool = False
    read_only: bool = False
    write_scope: list[str] | None = None
    rules: list[ExecRule] = Field(default_factory=list)
    max_recovery: int = Field(default=1, ge=0, le=5)
    max_final_revisions: int = Field(default=2, ge=0, le=5)
    completion: Literal["off", "warn", "soft", "strict"] = "strict"
    memory_enabled: bool = True
    memory_evolution: bool = True
    auto_dream: bool = True
    tool_profile: Literal["core", "all"] = "core"
    max_workers: int = Field(default=2, ge=0, le=4)

    @model_validator(mode="after")
    def valid_budget(self):
        output = self.effective_output_tokens
        if self.effective_context_tokens <= output + 256:
            raise ValueError("context_tokens must reserve output_tokens plus input space")
        if limits := MODEL_CONTEXT_LIMITS.get(self.model):
            if output > limits["output"]:
                raise ValueError("output_tokens exceeds the model output limit")
            if (
                self.effective_context_tokens > limits["window"]
                or self.effective_context_tokens - output > limits["input"]
            ):
                raise ValueError("context_tokens exceeds the model input/window limit")
        return self

    @property
    def effective_context_tokens(self) -> int:
        """Resolve on access so changing a model never retains another model's auto budget."""
        if self.context_tokens is not None:
            return self.context_tokens
        limits = MODEL_CONTEXT_LIMITS.get(self.model)
        if limits is None:
            return 24000
        output = self.effective_output_tokens
        input_limit = min(limits["input"], limits["window"] - output)
        # Local token estimates can differ from the provider's count.
        return input_limit * 95 // 100 + output

    @property
    def effective_output_tokens(self) -> int:
        if self.output_tokens is not None:
            return self.output_tokens
        limits = MODEL_CONTEXT_LIMITS.get(self.model)
        if self.context_tokens is not None:
            return min(32768, self.context_tokens // 4, limits["output"] if limits else 32768)
        return min(32768, limits["output"]) if limits else 2048

    @property
    def output_budget_source(self) -> str:
        if self.output_tokens is not None:
            return "explicit"
        if self.context_tokens is not None:
            return "context_budget"
        return "model_profile" if self.model in MODEL_CONTEXT_LIMITS else "fallback"

    @property
    def context_budget_source(self) -> str:
        if self.context_tokens is not None:
            return "explicit"
        return "model_profile" if self.model in MODEL_CONTEXT_LIMITS else "fallback"

    def identity(self) -> dict:
        """Checkpoints bind to effective boundaries, not credentials."""
        identity = self.model_dump(mode="json", exclude={"api_key", "base_url"})
        identity["context_tokens"] = self.effective_context_tokens
        identity["output_tokens"] = self.effective_output_tokens
        return identity


def read_env(path: Path) -> dict[str, str]:
    """Read literal KEY=value, without executing shell or expanding variables."""
    values = {}
    if path.exists():
        for line in path.read_text().splitlines():
            if "=" not in line or line.lstrip().startswith("#"):
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip("\"'")
    return values


def load_settings(
    env_file: Path | None = None, config_file: Path | None = None, **overrides
) -> Settings:
    values = tomllib.loads(config_file.read_text()) if config_file else {}
    env = {**(read_env(env_file) if env_file else {}), **os.environ}
    for name in ("protocol", "model", "base_url", "api_key"):
        if env.get(f"ASTER_{name.upper()}"):
            values[name] = env[f"ASTER_{name.upper()}"]
    values.update({k: v for k, v in overrides.items() if v is not None})
    return Settings.model_validate(values)
