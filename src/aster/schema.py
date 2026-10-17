"""Public data contracts; no filesystem, model, or UI dependencies."""

from __future__ import annotations

import hashlib
from typing import Annotated, Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class ToolCall(Record):
    kind: Literal["tool"] = "tool"
    name: str = Field(min_length=1, max_length=80)
    arguments: dict[str, Any] = Field(default_factory=dict)
    call_id: str = Field(default_factory=lambda: uuid4().hex, min_length=1, max_length=160)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=160)

    @model_validator(mode="before")
    @classmethod
    def host_operation_key(cls, values):
        if isinstance(values, dict) and isinstance(values.get("arguments"), dict):
            arguments = values["arguments"]
            if "_aster_idempotency_key" in arguments:
                arguments = dict(arguments)
                key = arguments.pop("_aster_idempotency_key")
                if values.get("idempotency_key") not in (None, key):
                    raise ValueError("conflicting idempotency keys")
                values = {**values, "arguments": arguments, "idempotency_key": key}
        return values


class Final(Record):
    kind: Literal["final"] = "final"
    answer: str = Field(min_length=1)


Action = Annotated[ToolCall | Final, Field(discriminator="kind")]
ACTION = TypeAdapter(Action)


class Outcome(Record):
    status: Literal["success", "error", "blocked", "timeout", "cancelled"]
    content: str = ""
    reason: str = ""
    exit_code: int | None = None
    changed_paths: list[str] = Field(default_factory=list)
    partial_success: bool = False
    duration_ms: float = 0
    evidence_id: str = ""
    artifact: str = ""
    data: dict[str, Any] = Field(default_factory=dict)


class TaskSpec(Record):
    prompt: str = Field(min_length=1)
    task_id: str = Field(default_factory=lambda: uuid4().hex)
    max_steps: int = Field(default=40, ge=1, le=500)
    max_turns: int = Field(default=40, ge=1, le=500)
    max_tokens: int | None = Field(default=None, ge=1)
    require_observation: bool = False
    require_edit: bool = False
    require_verification: bool = False
    required_artifacts: list[str] = Field(default_factory=list)


class Verification(Record):
    state: Literal["missing", "passed", "failed"] = "missing"
    workspace_hash: str = ""
    evidence_id: str = ""
    command: str = ""


class VerifierResult(Record):
    passed: bool
    reason: str
    details: dict[str, Any] = Field(default_factory=dict)


class SkillSnapshot(Record):
    body: str
    sha256: str

    @model_validator(mode="after")
    def validate_hash(self):
        if self.sha256 != hashlib.sha256(self.body.encode()).hexdigest():
            raise ValueError("skill_snapshot_hash_mismatch")
        return self


class RunState(Record):
    run_id: str = Field(default_factory=lambda: uuid4().hex)
    session_id: str = Field(default_factory=lambda: uuid4().hex)
    parent_run_id: str | None = None
    task: TaskSpec
    phase: Literal["ready", "running", "completed", "stopped", "failed"] = "ready"
    stop_reason: str = ""
    turn: int = 0
    step: int = 0
    observed: bool = False
    changed_paths: list[str] = Field(default_factory=list)
    verification: Verification = Field(default_factory=Verification)
    verification_failures: dict[str, str] = Field(default_factory=dict)
    environment_failures: dict[str, str] = Field(default_factory=dict)
    preexisting_failures: dict[str, str] = Field(default_factory=dict)
    # test id -> failure signature on the original files ("" = did not fail there)
    baseline_signatures: dict[str, str] = Field(default_factory=dict)
    final: str = ""
    feedback: str = ""
    final_revisions: int = 0
    mode: Literal["work", "plan"] = "work"
    plan: str = ""
    todos: list[dict[str, str]] = Field(default_factory=list)
    messages: list[dict[str, Any]] = Field(default_factory=list)
    active_skills: list[str] = Field(default_factory=list)
    skill_snapshots: dict[str, SkillSnapshot] = Field(default_factory=dict)
    working_memory: dict[str, dict[str, str]] = Field(default_factory=dict)
    recalled_ids: list[str] = Field(default_factory=list)
    read_evidence: dict[str, str] = Field(default_factory=dict)
    used_call_ids: list[str] = Field(default_factory=list)
    usage: dict[str, int] = Field(default_factory=dict)
    memory_usage: dict[str, int] = Field(default_factory=dict)
    team_usage: dict[str, int] = Field(default_factory=dict)
    pending_handoffs: dict[str, str] = Field(default_factory=dict)
    worker_reads: dict[str, str] = Field(default_factory=dict)
    handoff_sources: list[dict[str, Any]] = Field(default_factory=list)
    worker_sessions: dict[str, dict[str, Any]] = Field(default_factory=dict)
    loaded_tools: list[str] = Field(default_factory=list)
    user_inbox: list[dict[str, str]] = Field(default_factory=list)
    delivered_user_messages: list[str] = Field(default_factory=list)

    @property
    def terminal(self) -> bool:
        return self.phase in {"completed", "stopped", "failed"}

    @property
    def pending_requirements(self) -> list[dict[str, str]]:
        return [item for item in self.todos if item.get("acceptance")
                and item.get("status") not in {"done", "cancelled"}]

    @property
    def cancelled_requirements(self) -> list[dict[str, str]]:
        return [item for item in self.todos if item.get("acceptance")
                and item.get("status") == "cancelled"
                and item.get("closed_in_task") == self.task.task_id]


class ModelReply(Record):
    text: str = ""
    calls: list[ToolCall] = Field(default_factory=list)
    usage: dict[str, int] = Field(default_factory=dict)
    provider_model: str = ""
    diagnostics: dict[str, Any] = Field(default_factory=dict)
    raw: list[dict[str, Any]] = Field(default_factory=list)
