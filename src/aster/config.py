"""Explicit configuration. Repository text can never change runtime permissions."""

from __future__ import annotations

import os
import re
import tomllib
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import ConfigDict, Field, PrivateAttr, SecretStr, model_validator

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


class MCPServer(Record):
    """Explicitly trusted server; credentials are environment references only."""

    model_config = ConfigDict(hide_input_in_errors=True)
    command: str | None = Field(default=None, min_length=1)
    args: list[str] = Field(default_factory=list)
    cwd: str | None = None
    url: str | None = None
    env: dict[str, str] = Field(default_factory=dict)
    headers_env: dict[str, str] = Field(default_factory=dict)
    bearer_token_env: str | None = None
    enabled: bool = True
    enabled_tools: list[str] | None = None
    read_only_tools: list[str] = Field(default_factory=list)
    timeout: float = Field(default=30, gt=0, le=300)
    fallbacks: list[str] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def valid_server(self):
        if bool(self.command) == bool(self.url):
            raise ValueError("MCP server needs exactly one of command or url")
        if self.url:
            url = urlsplit(self.url)
            if (url.scheme not in {"https", "http"} or not url.hostname
                    or url.username is not None or url.password is not None
                    or url.query or url.fragment):
                raise ValueError("MCP url must be HTTP(S) without credentials, query or fragment")
            if url.scheme == "http" and url.hostname not in {"localhost", "127.0.0.1", "::1"}:
                raise ValueError("remote MCP requires HTTPS; HTTP is only allowed on loopback")
            if self.args or self.cwd or self.env:
                raise ValueError("args, cwd and env apply only to stdio MCP servers")
        elif self.headers_env or self.bearer_token_env:
            raise ValueError("HTTP credentials require an MCP url")
        for name in [*self.env, *self.env.values(), *self.headers_env.values(),
                     *([self.bearer_token_env] if self.bearer_token_env else [])]:
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
                raise ValueError("MCP credentials must name environment variables")
        if any(not re.fullmatch(r"[A-Za-z0-9-]+", key) for key in self.headers_env):
            raise ValueError("invalid MCP header name")
        if self.bearer_token_env and any(key.lower() == "authorization" for key in self.headers_env):
            raise ValueError("configure Authorization only once")
        return self

    def credential_names(self) -> set[str]:
        return {*self.env.values(), *self.headers_env.values(),
                *([self.bearer_token_env] if self.bearer_token_env else [])}


class ModelProfile(Record):
    """Provider coordinates and budgets; credentials are environment references only."""

    model_config = ConfigDict(hide_input_in_errors=True)
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    protocol: Literal["responses", "chat", "anthropic"]
    base_url: str
    auth: Literal["required", "none"] = "required"
    api_key_env: str | None = Field(default=None, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    context_tokens: int = Field(ge=1024)
    output_tokens: int = Field(ge=128)
    stream: bool = False

    @model_validator(mode="after")
    def valid_profile(self):
        url = urlsplit(self.base_url)
        if (
            url.scheme not in {"https", "http"}
            or not url.hostname
            or url.username is not None
            or url.password is not None
            or url.query
            or url.fragment
        ):
            raise ValueError(
                "base_url must be an HTTP(S) endpoint without credentials, query or fragment"
            )
        if self.auth == "required" and not self.api_key_env:
            raise ValueError("auth=required needs api_key_env")
        if self.auth == "none" and self.api_key_env:
            raise ValueError("auth=none cannot specify api_key_env")
        # Use the same budget and protocol checks as an effective configuration.
        Settings(
            model=self.model,
            protocol=self.protocol,
            context_tokens=self.context_tokens,
            output_tokens=self.output_tokens,
            stream=self.stream,
        )
        return self

    def settings_values(self, credentials: dict[str, SecretStr]) -> dict:
        secret = SecretStr("")
        if self.auth == "required":
            secret = credentials.get(
                self.api_key_env, SecretStr(os.environ.get(self.api_key_env, ""))
            )
            if not secret.get_secret_value():
                raise ValueError(
                    f"missing profile credential environment variable: {self.api_key_env}"
                )
        return {**self.model_dump(exclude={"api_key_env"}), "api_key": secret}


class Settings(Record):
    model_config = ConfigDict(hide_input_in_errors=True)
    provider: str = "openai"
    profile: str | None = None
    profiles: dict[
        Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")], ModelProfile
    ] = Field(default_factory=dict, exclude=True, repr=False)
    _profile_credentials: dict[str, SecretStr] = PrivateAttr(default_factory=dict)
    mcp_servers: dict[Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,23}$")], MCPServer] = Field(default_factory=dict)
    _mcp_credentials: dict[str, SecretStr] = PrivateAttr(default_factory=dict)
    protocol: Literal["responses", "chat", "anthropic"] = "responses"
    model: str = "gpt-5.6-luna"
    base_url: str = "https://api.openai.com/v1"
    api_key: SecretStr = Field(default_factory=lambda: SecretStr(""))
    auth: Literal["required", "none"] = "required"
    stream: bool = False
    request_timeout: float = Field(default=90, gt=0, le=300)
    circuit_failures: int = Field(default=3, ge=1, le=20)
    circuit_cooldown: float = Field(default=30, gt=0, le=300)
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
    # One-shot runs exit after the answer; 0 leaves memory consolidation for the next session.
    oneshot_maintenance_seconds: float = Field(default=0, ge=0, le=120)
    tool_profile: Literal["core", "all"] = "core"
    max_workers: int = Field(default=0, ge=0, le=4)
    read_parallelism: int = Field(default=1, ge=1, le=8)
    reuse_environment: bool = False

    @model_validator(mode="after")
    def valid_budget(self):
        visited = set()

        def visit(name, path):
            if name not in self.mcp_servers:
                raise ValueError(f"unknown MCP fallback server: {name}")
            if name in path:
                raise ValueError("MCP fallback cycle")
            if name in visited:
                return
            for backup in self.mcp_servers[name].fallbacks:
                visit(backup, {*path, name})
            visited.add(name)

        for name in self.mcp_servers:
            visit(name, set())
        if self.reuse_environment and self.sandbox != "required":
            raise ValueError("reuse_environment requires sandbox=required")
        if self.stream and self.protocol != "responses":
            raise ValueError("stream currently supports only the responses protocol")
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

    def select_profile(self, alias: str) -> Settings:
        """Resolve and validate a new selection without changing this configuration."""
        if alias not in self.profiles:
            raise ValueError(f"unknown model profile: {alias}")
        profile = self.profiles[alias]
        selected = Settings.model_validate(
            {
                **self.model_dump(),
                **profile.settings_values(self._profile_credentials),
                "profile": alias,
                "profiles": self.profiles,
            }
        )
        selected._profile_credentials = self._profile_credentials.copy()
        selected._mcp_credentials = self._mcp_credentials.copy()
        return selected

    def mcp_credential(self, name: str) -> str:
        return self._mcp_credentials.get(name, SecretStr(os.environ.get(name, ""))).get_secret_value()

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
        # Keep default identities compatible with checkpoints made before resilience options.
        for name, default in (("circuit_failures", 3), ("circuit_cooldown", 30)):
            if identity[name] == default:
                identity.pop(name)
        for server in identity["mcp_servers"].values():
            if not server["fallbacks"]:
                server.pop("fallbacks")
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
    env_file: Path | None = None,
    config_file: Path | None = None,
    *,
    profile: str | None = None,
    interactive: bool = False,
    **overrides,
) -> Settings:
    values = tomllib.loads(config_file.read_text()) if config_file else {}
    env = {**(read_env(env_file) if env_file else {}), **os.environ}
    configured_profile = values.pop("profile", None)
    alias = profile if profile is not None else configured_profile
    if alias is not None and not isinstance(alias, str):
        raise ValueError("profile must be a model profile name")
    if not isinstance(values.get("profiles", {}), dict):
        raise ValueError("profiles must be a TOML table")
    profiles = {
        name: ModelProfile.model_validate(
            {"stream": item.get("protocol") == "responses", **item}
            if interactive and isinstance(item, dict)
            else item
        )
        for name, item in values.get("profiles", {}).items()
    }
    credentials = {
        item.api_key_env: SecretStr(env[item.api_key_env])
        for item in profiles.values()
        if item.api_key_env and item.api_key_env in env
    }
    if alias is None:
        for name in ("protocol", "model", "base_url", "api_key"):
            if env.get(f"ASTER_{name.upper()}"):
                values[name] = env[f"ASTER_{name.upper()}"]
    else:
        if alias not in profiles:
            raise ValueError(f"unknown model profile: {alias}")
        values.update(profiles[alias].settings_values(credentials))
        values["profile"] = alias
    values["profiles"] = profiles
    values.update({k: v for k, v in overrides.items() if v is not None})
    if interactive:
        values.setdefault("stream", values.get("protocol", "responses") == "responses")
    result = Settings.model_validate(values)
    result._profile_credentials = credentials
    result._mcp_credentials = {
        name: SecretStr(env[name])
        for server in result.mcp_servers.values()
        for name in server.credential_names()
        if env.get(name)
    }
    return result
