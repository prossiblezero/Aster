"""Detached session preparation; Runtime owns committing state and resource lifetimes."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from ..config import Settings
from ..messages import history_groups
from ..safety.redaction import Redactor
from ..schema import RunState, SkillSnapshot
from ..storage.evidence import EvidenceStore
from ..storage.workspace import Workspace


def safety_identity(identity: dict) -> dict:
    identity = {"read_parallelism": 1, "reuse_environment": False, **identity}
    model_fields = {
        "provider",
        "profile",
        "model",
        "protocol",
        "auth",
        "stream",
        "context_tokens",
        "output_tokens",
    }
    return {key: value for key, value in identity.items() if key not in model_fields}


def redact_skill_snapshots(state: RunState, redactor: Redactor) -> None:
    # New model credentials may match text selected before the switch.
    if any(redactor.text(name) != name for name in {*state.active_skills, *state.skill_snapshots}):
        raise ValueError("skill_name_contains_credential")
    for name, snapshot in state.skill_snapshots.items():
        body = redactor.text(snapshot.body)
        if body != snapshot.body:
            state.skill_snapshots[name] = SkillSnapshot(
                body=body, sha256=hashlib.sha256(body.encode()).hexdigest()
            )


@dataclass(frozen=True)
class SessionCheckpoint:
    data: dict
    state: RunState
    history: list[dict]
    sources: dict[str, str]
    origins: list[dict]
    saved_identity: dict
    model_changed: bool


def load_checkpoint(
    evidence: EvidenceStore,
    workspace: Workspace,
    settings: Settings,
    run_id: str,
    *,
    allow_model_change: bool,
    adopt_workspace: bool,
) -> SessionCheckpoint:
    """Validate persisted identity, history and archive ownership before runtime mutation."""
    data = json.loads(evidence.path("runs", run_id, "checkpoint.json").read_text())
    if data.get("checkpoint_version", 1) not in {1, 2}:
        raise ValueError("unsupported_checkpoint_version: use the Aster version that saved this run")
    assert_settled_tools(evidence, run_id, data)
    # Opt-in workers share the workspace. Adopting edits cannot erase a child's
    # uncertain effects merely because worker_start already returned to the parent.
    for event in evidence.events(run_id):
        if event["event"] == "worker_started":
            child = event["child_run_id"]
            child_data = json.loads(evidence.path("runs", child, "checkpoint.json").read_text())
            if child_data["state"].get("parent_run_id") != run_id:
                raise ValueError("worker_checkpoint_identity_mismatch")
            assert_settled_tools(evidence, child, child_data)
    current_identity = settings.identity()
    # Old checkpoints predate named providers and streaming; their defaults are explicit.
    saved_identity = {
        "provider": "openai",
        "profile": None,
        "auth": "required",
        "stream": False,
        "read_parallelism": 1,
        "reuse_environment": False,
        **data["runtime_identity"],
    }
    model_changed = saved_identity != current_identity
    if model_changed and (
        not allow_model_change
        or safety_identity(saved_identity) != safety_identity(current_identity)
    ):
        differences = ", ".join(
            key for key in sorted(set(saved_identity) | set(current_identity))
            if saved_identity.get(key) != current_identity.get(key)
        )
        raise ValueError(
            "runtime_identity_mismatch: " + differences
            + ". Restore these explicit settings from the saved checkpoint; "
            "--allow-model-change permits only model settings, not safety or capability changes."
        )
    if data["workspace_hash"] != workspace.fingerprint() and not adopt_workspace:
        raise ValueError("workspace_mismatch")
    state = RunState.model_validate(data["state"])
    redact_skill_snapshots(state, evidence.redactor)
    state.messages = evidence.redactor.value(state.messages, mask_fields=False)
    if not state.terminal and "initial_text" not in data:
        raise ValueError("legacy_checkpoint_missing_diff_baseline")
    history_groups(data.get("structured_history", []))
    evidence.validate_native_history(data.get("structured_history", []))
    history = evidence.redact_history(data.get("structured_history", []))
    sources = data.get("archive_sources", {})
    if not isinstance(sources, dict):
        raise ValueError("invalid_archive_sources")
    for observation_id, source_run in sources.items():
        source = json.loads(evidence.path("runs", source_run, "checkpoint.json").read_text())
        if source["state"]["session_id"] != state.session_id or safety_identity(
            source["runtime_identity"]
        ) != safety_identity(current_identity):
            raise ValueError("archive_source_identity_mismatch")
        evidence.read_observation(source_run, observation_id, limit=1)
    return SessionCheckpoint(
        data=data,
        state=state,
        history=history,
        sources=sources,
        origins=list(data.get("history_origins", [])),
        saved_identity=saved_identity,
        model_changed=model_changed,
    )


def assert_settled_tools(evidence: EvidenceStore, run_id: str, data: dict) -> None:
    marker = evidence.path("runs", run_id, "inflight.json")
    if not marker.exists():
        return
    pending = json.loads(marker.read_text())
    answered = {
        item.get("call_id")
        for item in data.get("structured_history", [])
        if item.get("type") == "function_call_output"
    }
    calls = pending.get("calls", [])
    if not calls or any(call.get("call_id") not in answered for call in calls):
        raise ValueError(
            "interrupted_tool_batch: tool effects may have happened without a saved result. "
            "Inspect the workspace and .aster/runs/" + run_id
            + "/ events and inflight.json; start a new task with the confirmed state. "
            "Resume will not replay uncertain actions; --adopt-workspace cannot bypass this."
        )


def save_checkpoint(evidence, state, *, workspace_hash, workspace_snapshot, runtime_identity,
                    structured_history, initial_text, initial_modes, recovery_used, stall_items,
                    archive_sources, history_origins, continued_from):
    evidence.save(
        ("runs", state.run_id, "checkpoint.json"),
        {
            "checkpoint_version": 2,
            "state": state.model_dump(mode="json"),
            "workspace_hash": workspace_hash,
            "workspace": workspace_snapshot,
            "runtime_identity": runtime_identity,
            "structured_history": structured_history,
            "initial_text": initial_text,
            "initial_modes": initial_modes,
            "recovery_used": recovery_used,
            "stall_items": list(stall_items),
            "archive_sources": archive_sources,
            "history_origins": history_origins,
            "continued_from": continued_from,
        },
        preserve_native_history=True,
    )
