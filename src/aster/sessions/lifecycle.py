"""Detached session preparation; Runtime owns committing state and resource lifetimes."""

from __future__ import annotations

import json
from copy import deepcopy
from uuid import uuid4

from ..config import Settings
from ..context import ContextBuilder, ContextReport
from ..evidence import EvidenceStore
from ..messages import CanonicalHistory, canonicalize
from ..schema import Outcome, RunState, TaskSpec, Verification
from ..verifier import Verifier
from .checkpoint import redact_skill_snapshots


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
    *,
    environment: str = "",
    skills_available: bool = False,
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
        memory_enabled=settings.memory_enabled,
        skills_available=skills_available,
        environment=environment,
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


def read_session_observation(
    evidence: EvidenceStore, run_id: str, sources: dict[str, str], args: dict
) -> Outcome:
    """Read only this run and explicitly authorized ancestor observation IDs."""
    try:
        if args["observation_id"]:
            page = evidence.read_observation(
                sources.get(args["observation_id"], run_id),
                args["observation_id"],
                offset=args["offset"],
                limit=args["limit"],
                query=args["query"],
            )
        else:
            if not sources:
                page = evidence.list_observations(
                    run_id,
                    offset=args["offset"],
                    limit=min(10, args["limit"]),
                    query=args["query"],
                )
            else:
                records = {}
                for source_run in [run_id, *sorted(set(sources.values()))]:
                    offset = 0
                    while True:
                        listing = evidence.list_observations(
                            source_run, offset=offset, limit=20, query=args["query"]
                        )
                        for ref in listing["observations"]:
                            if (
                                source_run == run_id
                                or sources.get(ref["id"]) == source_run
                            ):
                                records[ref["id"]] = ref
                        offset = listing["next_offset"]
                        if offset is None:
                            break
                ordered = [records[key] for key in sorted(records)]
                start, end = args["offset"], args["offset"] + min(10, args["limit"])
                page = {
                    "observations": ordered[start:end],
                    "offset": start,
                    "next_offset": end if end < len(ordered) else None,
                    "total": len(ordered),
                }
    except (OSError, ValueError) as exc:
        return Outcome(status="blocked", reason="observation_unavailable", content=str(exc))
    # Wire previews need only the locator and paging fields; full provenance
    # stays in the archive and trace rather than every prompt.
    if args["observation_id"]:
        page = {
            key: value
            for key, value in page.items()
            if key not in {"sha256", "tool", "call_id"}
        }
    else:
        page["observations"] = [
            {key: value for key, value in ref.items() if key != "sha256"}
            for ref in page["observations"]
        ]
    return Outcome(status="success", content=json.dumps(page, ensure_ascii=False))



def continuation_sources(evidence, previous_run_id, sources):
    sources = dict(sources)
    directory = evidence.path("runs", previous_run_id, "observations")
    for path in directory.glob("*.json"):
        evidence.read_observation(previous_run_id, path.stem, limit=1)
        sources[path.stem] = previous_run_id
    return sources


def prepare_model_history(evidence, state, history, identity, settings, sources, origins, preview):
    """Validate and compact a candidate before archiving its native source."""
    evidence.validate_native_history(history)
    converted = canonicalize(history, provider=identity["provider"],
                             protocol=identity["protocol"], model=identity["model"])
    state = state.model_copy(deep=True)
    redact_skill_snapshots(state, evidence.redactor)
    state.messages = evidence.redactor.value(state.messages, mask_fields=False)
    candidate = evidence.redact_history(converted.messages)
    report = preview(state, candidate, settings, sources)
    origin = archive_native_history(evidence, state.run_id, history, converted, origins)
    return state, candidate, report, origin
