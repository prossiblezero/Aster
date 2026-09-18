"""Detached session preparation; Runtime owns committing state and resource lifetimes."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass
from uuid import uuid4

from .config import Settings
from .context import ContextBuilder, ContextReport
from .evidence import EvidenceStore, Redactor
from .history import CanonicalHistory
from .provider import history_groups
from .schema import RunState, SkillSnapshot, TaskSpec, Verification
from .verifier import Verifier
from .workspace import Workspace


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


def archive_native_history(
    evidence: EvidenceStore,
    run_id: str,
    history: list[dict],
    converted: CanonicalHistory,
    origins: list[dict],
) -> dict:
    artifact = ("runs", run_id, f"native-history-{len(origins) + 1}.json")
    evidence.save(
        artifact,
        {"source": converted.source, "structured_history": history},
        preserve_native_history=True,
    )
    return {
        "artifact": "/".join(artifact),
        "source": converted.source,
        "omitted_reasoning": converted.omitted_reasoning,
    }


def preview_context(
    evidence: EvidenceStore,
    state: RunState,
    history: list[dict],
    settings: Settings,
    sources: dict[str, str],
    tools: list[dict],
) -> ContextReport:
    """Budget-check detached state before Runtime publishes a session transition."""
    _, report = ContextBuilder(
        settings.effective_context_tokens, settings.effective_output_tokens
    ).build(
        state,
        state.task.prompt,
        structured_history=history,
        tools=tools,
        protocol=settings.protocol,
        model=settings.model,
        archive=lambda call_id, tool, content: evidence.archive_observation(
            state.run_id, call_id, tool, content
        ),
        validate_archive=lambda ref: evidence.read_observation(
            sources.get(ref["id"], state.run_id), ref["id"], limit=1
        ),
        redact_history=evidence.redact_history,
    )
    return report


def prepare_continuation(
    previous: RunState,
    history: list[dict],
    verifier: Verifier,
    *,
    feedback: str | None,
    drift: list[str],
    max_steps: int | None,
    max_turns: int | None,
) -> tuple[RunState, list[dict]]:
    """Copy a conversation into a fresh bounded run without mutating its predecessor."""
    state = previous.model_copy(deep=True)
    state.run_id = uuid4().hex
    state.phase, state.stop_reason, state.final, state.feedback = "running", "", "", ""
    state.step = state.turn = state.final_revisions = 0
    state.usage = {}
    history = deepcopy(history)
    if feedback is not None:
        inferred = verifier.infer(feedback)
        unfinished = previous.phase != "completed"
        if unfinished:
            inferred = {
                key: value or getattr(previous.task, key) for key, value in inferred.items()
            }
        if inferred["require_edit"] and previous.task.require_verification:
            inferred["require_verification"] = True
        state.task = TaskSpec(
            prompt=previous.task.prompt,
            max_steps=previous.task.max_steps,
            max_turns=previous.task.max_turns,
            required_artifacts=previous.task.required_artifacts
            if inferred["require_edit"] or unfinished
            else [],
            **inferred,
        )
        if not unfinished:
            state.observed, state.changed_paths = False, []
        history.append({"role": "user", "content": feedback})
        state.messages.append({"role": "user", "content": feedback, "important": True})
    limits = {
        key: value
        for key, value in {"max_steps": max_steps, "max_turns": max_turns}.items()
        if value is not None
    }
    if limits:
        state.task = TaskSpec.model_validate({**state.task.model_dump(), **limits})
    if feedback is not None or drift:
        state.verification = Verification()
        state.read_evidence, state.working_memory, state.recalled_ids = {}, {}, []
    if drift:
        note = (
            "User adopted external workspace edits; read current files before editing: "
            + ", ".join(drift)
        )
        history.append({"role": "user", "content": note})
    return state, history


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
        raise ValueError("runtime_identity_mismatch")
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
