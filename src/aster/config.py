"""Explicit configuration. Repository text can never change runtime permissions."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator

from .schema import Record


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
    context_tokens: int = Field(default=24000, ge=1024)
    output_tokens: int = Field(default=2048, ge=128)
    tool_output_tokens: int = Field(default=1600, ge=64)
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
        if self.context_tokens <= self.output_tokens + 256:
            raise ValueError("context_tokens must reserve output_tokens plus input space")
        return self

    def identity(self) -> dict:
        """Checkpoints bind to effective boundaries, not credentials."""
        return self.model_dump(mode="json", exclude={"api_key", "base_url"})


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
