"""One transition engine used by autonomous runs, CLI and the RL adapter."""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import inspect
import json
import re
import time
from collections.abc import Awaitable, Callable
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from ..config import Settings
from ..context import ContextBudgetError, ContextBuilder, ContextHistoryError
from ..knowledge.maintenance import MemoryMaintenance
from ..knowledge.memory import Memory
from ..knowledge.skills import Skills
from ..messages import history_groups
from ..providers.client import ModelClient
from ..providers.codec import request_payload
from ..providers.errors import ProviderError
from ..safety.policy import Decision, permission
from ..safety.redaction import Redactor
from ..safety.runner import SandboxRunner
from ..schema import (
    Outcome,
    RunState,
    TaskSpec,
    ToolCall,
    Verification,
    VerifierResult,
)
from ..sessions.checkpoint import (
    load_checkpoint,
    redact_skill_snapshots,
    safety_identity,
    save_checkpoint,
)
from ..sessions.lifecycle import (
    archive_native_history,
    continuation_sources,
    prepare_continuation,
    prepare_model_history,
    preview_context,
)
from ..sessions.tree import SessionTree
from ..storage.evidence import EvidenceStore
from ..storage.operations import OperationJournal
from ..storage.workspace import Workspace
from ..tools import (
    CORE_TOOLS,
    ToolContext,
    ToolRegistry,
    definitions,
    read_edit_source,
)
from ..tools.availability import unavailable_reason
from ..tools.mcp import MCPTools
from ..tools.registry import validate_arguments
from ..tools.workflow import WorkflowTools
from .execution import execute_reads
from .hooks import Hooks
from .recovery import StallDetector, completion_feedback
from .verifier import Verifier
from .workers import WorkerManager


class Runtime:
    def __init__(
        self,
        root: Path,
        settings: Settings,
        *,
        model: ModelClient | None = None,
        approval_callback: Callable[[ToolCall, Decision], bool | Awaitable[bool]] | None = None,
        question_callback: Callable[[str], str | None | Awaitable[str | None]] | None = None,
        hooks: Hooks | None = None,
        memory_model=None,
    ):
        self.workspace = Workspace(root)
        self.settings = settings
        self._initial_settings = settings.model_copy(deep=True)
        self.approval_callback = approval_callback
        self.question_callback = question_callback
        secrets = [settings.api_key.get_secret_value()]
        secrets.extend(settings.mcp_credential(name) for server in settings.mcp_servers.values()
                       for name in server.credential_names())
        self.evidence = EvidenceStore(self.workspace.root, Redactor(secrets))
        self.session_tree = SessionTree(self.evidence)
        self.memory = Memory(
            self.workspace.root, settings.memory_enabled, settings.memory_evolution, secrets=secrets
        )
        self.model = model or ModelClient(settings)
        if isinstance(self.model, ModelClient):
            self.model.redaction_secrets = secrets
        self.mcp = MCPTools(settings, self.workspace.root, self._unavailable_reason)
        self.sandbox = SandboxRunner(settings)
        self.skills = Skills(self.workspace.root)
        self.context = ContextBuilder(
            settings.effective_context_tokens, settings.effective_output_tokens
        )
        self.hooks = hooks or Hooks()
        self.verifier = Verifier(self.workspace)
        self.stall = StallDetector()
        self.state: RunState | None = None
        self._initial_text: dict[str, str] = {}
        self._initial_modes: dict[str, int] = {}
        self._in_turn = False
        self._pending_messages: list[dict] = []
        self._structured_history: list[dict] = []
        self._recovery_used = 0
        self._finish_hook_ran = False
        self._dream_ran = False
        self._operation_task: asyncio.Task | None = None
        self._managed_worker = False
        self._handoff_reason = ""
        self._worker_owner: WorkerManager | None = None
        self._request_estimate = 0
        self._archive_sources: dict[str, str] = {}
        self._snapshot_sources: dict[str, str] | None = None
        self._history_origins: list[dict] = []
        self._continued_from: str | None = None
        self._checkpoint_workspace: dict[str, str] = {}
        self._environment_generation = self.sandbox.environment_generation
        self.workers = WorkerManager(self)
        self.maintenance = MemoryMaintenance(self, memory_model)

    @property
    def _team(self):
        return self._worker_owner or self.workers

    @contextmanager
    def _operation(self):
        """One task owns transitions; run -> turn -> step may nest in that task."""
        if self._snapshot_sources is not None:
            raise RuntimeError("snapshot_requires_continuation: call continue_task before executing")
        task = asyncio.current_task()
        if self._operation_task is not None and self._operation_task is not task:
            raise RuntimeError("runtime_busy: await the current operation")
        owner = self._operation_task is None
        lease = None
        if owner:
            # Independent sessions must use separate workspaces while running.
            # Explicit subordinate workers remain governed by WorkerManager scopes.
            if not self._managed_worker:
                lease = self.evidence.path("workspace.lock").open("a")
                try:
                    fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    lease.close()
                    raise RuntimeError("workspace_busy: use a separate directory or worktree") from None
            self._operation_task = task
        try:
            if owner and lease is not None and self.memory.enabled:
                # Another idle session may have committed memory since this Runtime
                # was opened. Refresh under the workspace lease before reading/writing.
                memory_state = Memory(
                    self.workspace.root, self.memory.enabled, self.memory.evolution,
                    secrets=self.memory.redactor.secrets,
                ).state
                if memory_state != self.memory.state:
                    self.memory.state = memory_state
                self.maintenance.pulse(owned=True, start=False)
            yield
        except asyncio.CancelledError:
            self.finish("cancelled")
            raise
        finally:
            if owner:
                self._operation_task = None
                try:
                    if lease is not None:
                        self.maintenance.pulse(owned=True, start=bool(self.state and self.state.terminal))
                    if self.state and self.state.phase in {"stopped", "failed"}:
                        # Refresh final usage after cancellation settles.
                        self.report(VerifierResult(passed=False, reason=self.state.stop_reason))
                    else:
                        self.checkpoint()
                finally:
                    if lease is not None:
                        lease.close()

    def _ensure_idle(self):
        if self._operation_task or any(not w.future.done() for w in self.workers.workers.values()):
            raise RuntimeError("runtime_busy: await current operations and workers")

    def switch_model(self, alias: str) -> Settings:
        """Switch at an idle boundary, validating portable history and the new budget first."""
        self._ensure_idle()
        settings = self.settings.select_profile(alias)
        model = ModelClient(settings)
        context = ContextBuilder(
            settings.effective_context_tokens, settings.effective_output_tokens
        )
        redactor = Redactor([*self.evidence.redactor.secrets, settings.api_key.get_secret_value()])
        model.redaction_secrets = redactor.secrets
        previous = self.settings.identity()
        state, history, report = self.state, self._structured_history, None
        origin = None
        old_redactor = self.evidence.redactor
        try:
            # Preflight may archive compacted observations. New credentials must be
            # protected before any persistence, including native opaque validation.
            self.evidence.redactor = redactor
            if state:
                if Workspace.changes(self._checkpoint_workspace, self.workspace.snapshot()):
                    raise ValueError(
                        "workspace_mismatch: adopt external edits before switching models"
                    )
                state, history, report, origin = prepare_model_history(
                    self.evidence, state, history, previous, settings,
                    self._archive_sources, self._history_origins, self._preview_context,
                )
        finally:
            self.evidence.redactor = old_redactor
        self.settings, self.model, self.context = settings, model, context
        self.state, self._structured_history = state, history
        self.sandbox.settings = settings
        self.mcp.settings = settings
        self.evidence.redactor = self.memory.redactor = redactor
        if self.state:
            self._history_origins.append(origin)
            self.evidence.emit(
                self.state.run_id,
                "model_changed",
                previous=previous,
                selected=settings.identity(),
                origin=origin,
                compacted=report.compacted,
                estimated_tokens=report.estimated_tokens,
            )
            self.checkpoint()
        return settings

    def _archive_native_history(self, run_id, history, converted, origins):
        return archive_native_history(self.evidence, run_id, history, converted, origins)

    def _preview_context(self, state, history, settings, sources):
        tools = self._request_tool_definitions(settings, state=state)
        return preview_context(
            self.evidence, state, history, settings, sources, tools,
            environment=self.sandbox.capabilities(), skills_available=bool(self.skills.catalog()),
        )

    @staticmethod
    def _safety_identity(identity):
        return safety_identity(identity)

    def new_session(self):
        self._ensure_idle()
        if self.state and not self.state.terminal:
            self.finish("new_session")
        self.sandbox.close()
        self._environment_generation = self.sandbox.environment_generation
        self.state = None
        self.session_tree.reset()
        self._structured_history, self._pending_messages = [], []
        self._archive_sources, self._history_origins = {}, []
        self._snapshot_sources = None

    def continue_task(
        self,
        feedback: str | None = None,
        *,
        adopt_workspace: bool = False,
        max_steps: int | None = None,
        max_turns: int | None = None,
    ) -> RunState:
        """An explicit continuation gets a bounded run in the same persistent conversation."""
        self._ensure_idle()
        if self.state is None:
            raise ValueError("no_session: start or resume a task first")
        if feedback is not None and not feedback.strip():
            raise ValueError("empty_feedback")
        self._deliver_user_messages()
        previous = self.state
        current = self.workspace.snapshot()
        drift = Workspace.changes(self._checkpoint_workspace, current)
        if drift and not adopt_workspace:
            raise ValueError("workspace_mismatch: " + ", ".join(drift[:20]))
        state, history = prepare_continuation(
            previous,
            self._structured_history,
            self.verifier,
            feedback=feedback,
            drift=drift,
            max_steps=max_steps,
            max_turns=max_turns,
        )
        sources = (dict(self._snapshot_sources) if self._snapshot_sources is not None else
                   continuation_sources(self.evidence, previous.run_id, self._archive_sources))
        self._preview_context(state, history, self.settings, sources)
        initial_text = (
            self.workspace.text_snapshot() if feedback is not None or drift else self._initial_text
        )
        initial_modes = (
            self.workspace.mode_snapshot() if feedback is not None or drift else self._initial_modes
        )
        self.state, self._structured_history, self._archive_sources = state, history, sources
        self._snapshot_sources = None
        self._continued_from = previous.run_id
        if feedback is not None:
            self.session_tree.turn_id = state.run_id
        self.session_tree.pending.append(("turn_start" if feedback is not None else "run_continued", {
            "prompt": feedback, "continued_from": previous.run_id,
        }))
        self._initial_text, self._initial_modes = initial_text, initial_modes
        self._pending_messages = []
        self.workers = WorkerManager(self)
        self._handoff_reason = ""
        self.stall.items.clear()
        self._recovery_used = 0
        self._finish_hook_ran = self._dream_ran = False
        self.evidence.emit(
            state.run_id,
            "run_continued",
            continued_from=previous.run_id,
            session_id=state.session_id,
            feedback=feedback,
            adopted_paths=drift,
            task=state.task.model_dump(),
            config=self.settings.identity(),
        )
        self.checkpoint()
        return state

    def start(self, task: TaskSpec) -> RunState:
        self._ensure_idle()
        if self.state and not self.state.terminal:
            self.finish("superseded")
        self.sandbox.close()
        self._environment_generation = self.sandbox.environment_generation
        task = task.model_copy(deep=True)
        inferred = self.verifier.infer(task.prompt)
        for key, value in inferred.items():
            if key not in task.model_fields_set:
                setattr(task, key, value)
        self.state = RunState(task=task, phase="running")
        self.session_tree.reset()
        self.session_tree.turn_id = self.state.run_id
        self.session_tree.pending.append(("turn_start", {"prompt": task.prompt}))
        self.workers = WorkerManager(self)
        self._handoff_reason = ""
        self._initial_text = self.workspace.text_snapshot()
        self._initial_modes = self.workspace.mode_snapshot()
        self.stall.items.clear()
        self._recovery_used = 0
        # The first turn is rebuilt by ContextBuilder so system policy and untrusted-data
        # boundaries are actually sent to the model; this seed is only for the task state.
        self._structured_history = []
        self._archive_sources = {}
        self._snapshot_sources = None
        self._history_origins = []
        self._continued_from = None
        self._pending_messages = []
        self._finish_hook_ran = False
        self._dream_ran = False
        self.evidence.emit(
            self.state.run_id,
            "run_started",
            task=task.model_dump(),
            workspace_hash=self.workspace.fingerprint(),
            config=self.settings.identity(),
        )
        self.checkpoint()
        return self.state

    def checkpoint(self):
        if not self.state or self._in_turn or self._snapshot_sources is not None:
            return
        self._redact_skill_snapshots(self.state)
        history_groups(self._structured_history)
        workspace = self.workspace.snapshot()
        save_checkpoint(
            self.evidence, self.state, workspace_hash=self.workspace.fingerprint(),
            workspace_snapshot=workspace, runtime_identity=self.settings.identity(),
            structured_history=self._structured_history, initial_text=self._initial_text,
            initial_modes=self._initial_modes, recovery_used=self._recovery_used,
            stall_items=self.stall.items, archive_sources=self._archive_sources,
            history_origins=self._history_origins, continued_from=self._continued_from,
            commit=self._commit_session,
        )
        self._checkpoint_workspace = workspace

    def _commit_session(self, data):
        # Freeze the observation allowlist now; later observations on a sibling run
        # must not become readable when an earlier boundary is selected.
        data["session_observations"] = {
            path.stem: self.state.run_id
            for path in self.evidence.path("runs", self.state.run_id, "observations").glob("*.json")
        }
        return self.session_tree.commit(data)

    def checkout(self, entry_id: str, *, summary=False, adopt_workspace=False):
        """Select a settled historical boundary, restoring model settings but never files."""
        from ..sessions.navigation import checkout
        return checkout(self, entry_id, summary=summary, adopt_workspace=adopt_workspace)

    def resume(
        self, run_id: str, *, allow_model_change: bool = False, adopt_workspace: bool = False,
        entry_id: str | None = None,
    ) -> RunState:
        """Resume a run; a pinned historical entry must continue into a fresh run before execution."""
        self._ensure_idle()
        if self.state and not self.state.terminal:
            raise RuntimeError("active_run: finish the current run before resuming another")
        snapshot = self.session_tree.restore(entry_id) if entry_id else None
        if snapshot is not None:
            if snapshot["state"]["run_id"] != run_id:
                raise ValueError("session_snapshot_identity_mismatch")
            snapshot["archive_sources"] = {**snapshot["archive_sources"], **snapshot.get("session_observations", {})}
        saved = load_checkpoint(
            self.evidence,
            self.workspace,
            self.settings,
            run_id,
            allow_model_change=allow_model_change,
            adopt_workspace=adopt_workspace,
            data=snapshot,
        )
        data, state, history = saved.data, saved.state, saved.history
        sources, origins = saved.sources, saved.origins
        saved_identity, model_changed = saved.saved_identity, saved.model_changed
        current_identity = self.settings.identity()
        if model_changed:
            state, history, _, origin = prepare_model_history(
                self.evidence, state, history, saved_identity, self.settings,
                sources, origins, self._preview_context,
            )
            origins.append(origin)
        self.session_tree.attach(data)
        self.sandbox.close()
        self._environment_generation = self.sandbox.environment_generation
        if self.settings.reuse_environment:
            # Execution roots are process-local; persisted tests cannot attest to a
            # dependency environment that was not restored with this checkpoint.
            state.verification = Verification()
            state.working_memory.pop("__run_tests__", None)
        self.state = state
        self.workers = WorkerManager(self)
        self._handoff_reason = ""
        self._initial_text = data.get("initial_text", {})
        self._initial_modes = data.get("initial_modes", {})
        self._recovery_used = data.get("recovery_used", 0)
        self.stall.items.clear()
        self.stall.items.extend(tuple(item) for item in data.get("stall_items", []))
        self._finish_hook_ran = False
        self._dream_ran = False
        self._pending_messages = []
        self._structured_history = history
        self._archive_sources = sources
        # A historical child continuation may only inherit observations visible at that boundary.
        self._snapshot_sources = dict(sources) if entry_id else None
        self._history_origins = origins
        self._continued_from = data.get("continued_from")
        self._checkpoint_workspace = data["workspace"]
        if entry_id is None:
            mailbox = self.evidence.path("runs", run_id, "user-inbox.json")
            if mailbox.exists():
                known = set(state.delivered_user_messages) | {item["id"] for item in state.user_inbox}
                state.user_inbox.extend(item for item in json.loads(mailbox.read_text()) if item["id"] not in known)
        if adopt_workspace:
            self._refresh_observations()
        if model_changed:
            self.evidence.emit(
                state.run_id,
                "resume_model_changed",
                previous=saved_identity,
                selected=current_identity,
            )
        return self.state

    async def run(self, task: TaskSpec | None = None, *, max_steps: int | None = None) -> RunState:
        if task is not None:
            self.start(task)
        with self._operation():
            return await self._run(max_steps=max_steps)

    async def _run(self, *, max_steps: int | None = None) -> RunState:
        if not self.state:
            raise RuntimeError("start(task) first")
        limit = max_steps or self.state.task.max_steps
        try:
            while (
                not self.state.terminal
                and self.state.step < limit
                and self.state.turn < self.state.task.max_turns
            ):
                await self.turn()
            if not self.state.terminal:
                self.finish(
                    "turn_limit" if self.state.turn >= self.state.task.max_turns else "step_limit"
                )
            return self.state
        except asyncio.CancelledError:
            self.finish("cancelled")
            raise
        except Exception as exc:
            self.state.feedback = f"runtime_error:{type(exc).__name__}"
            self.finish("runtime_error", failed=True)
            raise
        finally:
            await self.workers.close()

    async def turn(self) -> RunState:
        with self._operation():
            return await self._turn()

    async def _turn(self) -> RunState:
        state = self.state
        assert state is not None
        if state.terminal:
            return state
        self._deliver_user_messages()
        if state.turn >= state.task.max_turns:
            self.finish("turn_limit")
            return state
        await self.mcp.restore(state.loaded_tools)
        try:
            prompt, tool_defs, report = self.prepare_request()
        except (ContextBudgetError, ContextHistoryError) as exc:
            state.feedback = self.evidence.redactor.text(str(exc))
            self.finish(
                "context_history_invalid"
                if isinstance(exc, ContextHistoryError)
                else "context_budget_exceeded"
            )
            return state
        except (OSError, ValueError) as exc:
            state.feedback = f"context_archive_unavailable:{type(exc).__name__}"
            self.finish("context_archive_unavailable", failed=True)
            return state
        state.turn += 1
        return await self._model_turn(prompt, tool_defs)

    def prepare_request(self):
        """One budgeted policy observation for autonomous calls and external rollouts."""
        state = self.state
        assert state is not None
        self._deliver_user_messages()
        if self._operation_task is not None:
            self.maintenance.pulse(owned=True, start=False)
        self.workers.deliver()
        # Settings are mutable; the counted request must use the provider's current model limits.
        self.context.budget = self.settings.effective_context_tokens
        self.context.output_reserve = self.settings.effective_output_tokens
        self._refresh_observations()
        memory_text = self._memory_text(state.task.prompt)
        working_memory = self.memory.working_section(state.working_memory)
        catalog = self.skills.catalog()
        skills = (
            self.evidence.redactor.text(json.dumps(catalog, ensure_ascii=False)) if catalog else ""
        )
        self._redact_skill_snapshots(state)
        skill_bodies = {
            name: state.skill_snapshots[name].body
            if name in state.skill_snapshots
            else "[Skill snapshot unavailable; explicitly select it with skill_load.]"
            for name in dict.fromkeys(state.active_skills)
        }
        tool_defs = self._request_tool_definitions(self.settings)

        def validate_archive(ref):
            # The archive id already hashes provenance and body; read verifies both.
            self.evidence.read_observation(
                self._archive_sources.get(ref["id"], state.run_id), ref["id"], limit=1
            )

        prompt, report = self.context.build(
            state,
            state.task.prompt,
            memory_text,
            skills,
            self._structured_history,
            working_memory,
            tools=tool_defs,
            protocol=self.settings.protocol,
            model=self.settings.model,
            memory_enabled=self.settings.memory_enabled,
            skills_available=bool(catalog),
            workers_available=self.settings.max_workers > 0 or bool(state.worker_sessions),
            environment=self.sandbox.capabilities(),
            skill_bodies=skill_bodies,
            archive=lambda call_id, tool, content: self.evidence.archive_observation(
                state.run_id, call_id, tool, content
            ),
            validate_archive=validate_archive,
            redact_history=self.evidence.redact_history,
        )
        self._request_estimate = report.estimated_tokens + self.settings.effective_output_tokens
        team = self._team
        token_limit = team.parent.state.task.max_tokens
        if (self._managed_worker and not self._handoff_reason and token_limit is not None
                and team.parent.state.team_usage.get("charged_tokens", 0)
                + team.budget.token_reserve() + 2 * self._request_estimate > token_limit):
            # ponytail: two current-sized requests; context growth still faces hard admission.
            self._begin_handoff("team_token_limit")
            return self.prepare_request()
        if (not self._managed_worker and not self._handoff_reason and (state.turn or state.step)
                and (team.budget.remaining("model_calls") <= 1
                     or (token_limit is not None and state.team_usage.get("charged_tokens", 0)
                         + 2 * self._request_estimate > token_limit))):
            self._begin_handoff("team_token_limit" if token_limit is not None
                                and state.team_usage.get("charged_tokens", 0)
                                + 2 * self._request_estimate > token_limit else "team_model_calls_limit")
            return self.prepare_request()
        if self._operation_task is not None and not self._handoff_reason:
            self.maintenance.pulse(owned=True)
        self.checkpoint()
        payload = request_payload(
            prompt,
            tool_defs,
            protocol=self.settings.protocol,
            model=self.settings.model,
            output_tokens=self.settings.effective_output_tokens,
        )
        # Keep the actual model observation apart from the full execution trace.
        request_hash = self._request_hash(payload)
        artifact = ("runs", state.run_id, "requests", f"{state.turn + 1:04d}-{request_hash}.json")
        self.evidence.save(artifact, {"policy_version": report.policy_version, "payload": payload})
        self.evidence.emit(
            state.run_id,
            "context_built",
            estimated_tokens=report.estimated_tokens,
            budget=report.budget,
            compacted=report.compacted,
            removed_messages=report.removed_messages,
            sections=report.sections,
            policy_version=report.policy_version,
            archive_hashes=report.archive_hashes,
            request_artifact="/".join(artifact),
            request_sha256=request_hash,
        )
        return prompt, tool_defs, report

    def _begin_handoff(self, reason):
        self._handoff_reason = reason
        self.add_message(
            "user", "Your work budget is exhausted or nearly exhausted. Do not call tools; only final delivery is permitted. "
            + ("Return a concise PARTIAL handoff" if self._managed_worker else
               "Deliver the result now, clearly marking incomplete work") + " using only observed facts: findings, "
            "paths/symbols, changes, checks actually run, and remaining gaps. "
            + ("Do not claim completion." if self._managed_worker else
               "Claim completion only when the requested work and checks are satisfied."), important=True,
        )

    def _redact_skill_snapshots(self, state):
        redact_skill_snapshots(state, self.evidence.redactor)

    def _request_hash(self, payload):
        return hashlib.sha256(
            json.dumps(
                self.evidence.redactor.value(payload, mask_fields=False),
                sort_keys=True,
                ensure_ascii=False,
            ).encode()
        ).hexdigest()

    async def _model_turn(self, prompt, tool_defs):
        state = self.state
        assert state is not None
        handoff_request = bool(self._handoff_reason)
        reservation = self._request_estimate
        reason = self._team.budget.admit(self, "model_calls", reservation)
        if reason:
            self.finish(reason)
            return state
        self._team.parent.checkpoint()
        request_hash = self._request_hash(
            request_payload(
                prompt,
                tool_defs,
                protocol=self.settings.protocol,
                model=self.settings.model,
                output_tokens=self.settings.effective_output_tokens,
            )
        )
        request_usage = {}
        try:
            request_messages = prompt
            if isinstance(self.model, ModelClient):
                reply = await self.model.complete(
                    request_messages,
                    tool_defs,
                    on_event=lambda event: self.evidence.emit(
                        state.run_id,
                        "provider_attempt" if event.get("type") == "diagnostic" else (
                            "model_stream" if self.settings.stream else "model_provider"
                        ),
                        **event,
                    ),
                )
            else:
                reply = await self.model.complete(request_messages, tool_defs)
            request_usage = reply.usage
        except asyncio.CancelledError:
            if isinstance(self.model, ModelClient):
                request_usage = self.model.last_usage
                for name, value in self.model.last_usage.items():
                    state.usage[name] = state.usage.get(name, 0) + value
            raise
        except ProviderError as exc:
            request_usage = exc.details.get("usage", {})
            state.feedback = self.evidence.redactor.text(str(exc))
            for name, value in exc.details.get("usage", {}).items():
                state.usage[name] = state.usage.get(name, 0) + value
            self.evidence.emit(
                state.run_id, "provider_error", error=str(exc), **getattr(exc, "details", {})
            )
            self.finish("provider_error", failed=True)
            return state
        finally:
            self._team.budget.settle(reservation, request_usage)
            self._team.parent.checkpoint()
        for name, value in reply.usage.items():
            state.usage[name] = state.usage.get(name, 0) + value
        if state.terminal:
            return state
        ids = [call.call_id for call in reply.calls]
        previous = set(state.used_call_ids) | {
            item["call_id"]
            for item in self._structured_history
            if item.get("type") == "function_call_output"
        }
        if (
            any(not call_id.strip() for call_id in ids)
            or len(ids) != len(set(ids))
            or previous.intersection(ids)
        ):
            state.feedback = "invalid_tool_id: model returned an empty or reused call id"
            self.finish("context_history_invalid", failed=True)
            return state
        if reply.calls:
            self._mark_inflight(reply.calls)
        self._structured_history.extend(reply.raw)
        # Test policies may omit wire records; real providers preserve their native metadata.
        if reply.calls and not any(
            item.get("type") == "function_call"
            or item.get("tool_calls")
            or any(
                b.get("type") == "tool_use" for b in item.get("content", []) if isinstance(b, dict)
            )
            for item in reply.raw
        ):
            self._structured_history.extend(
                {
                    "type": "function_call",
                    "call_id": c.call_id,
                    "name": c.name,
                    "arguments": json.dumps(c.arguments),
                }
                for c in reply.calls
            )
        if reply.text:
            if not reply.raw:
                self._structured_history.append({"role": "assistant", "content": reply.text})
            self.add_message("assistant", reply.text, important=True, native=True)
        self.evidence.emit(
            state.run_id,
            "model_reply",
            turn=state.turn,
            request_sha256=request_hash,
            text=reply.text,
            calls=[c.model_dump() for c in reply.calls],
            usage=reply.usage,
            response_model=reply.diagnostics.get("response_model", reply.provider_model),
            diagnostics=reply.diagnostics,
        )
        if not reply.calls:
            if state.user_inbox:
                self._deliver_user_messages()
                self.checkpoint()
                return state
            if reply.text:
                if self._handoff_reason:
                    if not self._managed_worker and self.verifier.final(state).passed:
                        self.finalize(reply.text)
                        if state.terminal:
                            return state
                    state.final = self.evidence.redactor.text(reply.text)
                    if not self._managed_worker:
                        state.final += "\n\n任务未全部完成：预算不足，以上仅为已完成的工作与检查。"
                    self.finish(self._handoff_reason)
                    return state
                return self.finalize(reply.text)
            state.feedback = "Model returned neither text nor a tool call."
            self.finish("empty_model_reply", failed=True)
            return state
        self._in_turn = True
        batch_started = time.monotonic()
        try:
            await self._execute_batch(reply.calls, on_result=self._append_output)
        finally:
            self.evidence.emit(
                state.run_id,
                "tool_batch_finished",
                calls=len(reply.calls),
                duration_ms=(time.monotonic() - batch_started) * 1000,
                read_parallelism=self.settings.read_parallelism,
            )
            # Never persist a half call/result batch, even on cancellation.
            answered = {
                x.get("call_id")
                for x in self._structured_history
                if x.get("type") == "function_call_output"
            }
            for call in reply.calls:
                if call.call_id not in answered:
                    self._append_output(
                        call,
                        Outcome(
                            status="cancelled",
                            reason="turn_interrupted",
                            content="Interrupted; inspect workspace before assuming side effects completed.",
                        ),
                    )
            self._structured_history.extend(self._pending_messages)
            self._pending_messages.clear()
            self._in_turn = False
            self._deliver_user_messages()
            self.checkpoint()
            self.evidence.path("runs", state.run_id, "inflight.json").unlink(missing_ok=True)
        if handoff_request and not state.terminal:
            if not self._managed_worker:
                state.final = ("任务未全部完成：预算不足，已停止继续执行。\n"
                               + "已修改文件：" + (", ".join(state.changed_paths) or "无")
                               + "；验证状态：" + state.verification.state + "。")
            self.finish(self._handoff_reason)
        return state

    def _mark_inflight(self, calls: list[ToolCall]) -> None:
        # The last complete checkpoint stays readable. A crash before results are
        # committed must never make an uncertain side effect look safe to replay.
        self.evidence.save(
            ("runs", self.state.run_id, "inflight.json"),
            {"calls": [{"call_id": call.call_id, "tool": call.name} for call in calls]},
        )

    def _append_output(self, call: ToolCall, outcome: Outcome):
        content = outcome.content
        if call.name == "worker_status" and outcome.status == "success":
            self.workers.acknowledge(outcome.data)
            content = json.dumps(self.workers.model_view(outcome.data), ensure_ascii=False)
        content = self.evidence.redactor.text(content)
        if call.name == "read_file" and outcome.status == "success":
            content = "\n".join(
                f"{line}: {text}"
                for line, text in enumerate(content.splitlines(), outcome.data["start"])
            )
        output = json.dumps(
            {
                "status": outcome.status,
                "reason": outcome.reason,
                "evidence_id": outcome.evidence_id,
                "changed_paths": outcome.changed_paths,
                "content": content,
                **{key: outcome.data[key] for key in (
                    "idempotency_replayed", "original_status", "uncertain", "mcp_endpoint", "failover",
                ) if key in outcome.data},
                **(
                    {key: outcome.data[key] for key in ("path", "start", "end", "source_sha256")}
                    if call.name == "read_file" and outcome.status == "success"
                    else {}
                ),
                **(
                    {"source_ref": outcome.evidence_id[:12]}
                    if self._managed_worker and call.name == "read_file" and outcome.status == "success"
                    else {}
                ),
                **(
                    {"observation": outcome.data["observation"]}
                    if "observation" in outcome.data
                    else {}
                ),
                **(
                    {
                        key: outcome.data[key]
                        for key in (
                            "output_bytes",
                            "captured_bytes",
                            "output_truncated",
                            "output_limit_bytes",
                        )
                        if key in outcome.data
                    }
                    if call.name in {"run_shell", "run_tests"}
                    else {}
                ),
                **(
                    {
                        key: outcome.data[key]
                        for key in ("offset", "count", "total", "truncated", "next_offset")
                        if key in outcome.data
                    }
                    if call.name in {"list_files", "search"} and outcome.status == "success"
                    else {}
                ),
                **(
                    {"page": "observation" if call.name == "observation_read" else "text"}
                    if call.name in {"observation_read", "todo_list", "skill_load"}
                    and outcome.status == "success"
                    else {}
                ),
                **(
                    {
                        key: outcome.data[key]
                        for key in ("version", "previous_version", "updated")
                        if key in outcome.data
                    }
                    if call.name == "skill_load" and outcome.status == "success"
                    else {}
                ),
            },
            ensure_ascii=False,
        )
        if outcome.data.get("image_data"):
            output = [
                {"type": "input_text", "text": outcome.content},
                {"type": "input_image", "image_url": outcome.data["image_data"]},
            ]
        self._structured_history.append(
            {"type": "function_call_output", "call_id": call.call_id, "output": output}
        )

    async def step(self, call: ToolCall) -> Outcome:
        with self._operation():
            if self._in_turn or (self.state and self.state.terminal):
                return await self._step(call)
            if not call.call_id.strip():
                return Outcome(status="blocked", reason="invalid_tool_id")
            if self.state and (
                call.call_id in self.state.used_call_ids
                or any(
                    item.get("type") == "function_call_output" and item["call_id"] == call.call_id
                    for item in self._structured_history
                )
            ):
                self.evidence.emit(
                    self.state.run_id,
                    "tool_rejected",
                    call_id=call.call_id,
                    reason="duplicate_call_id",
                )
                return Outcome(status="blocked", reason="duplicate_call_id")
            # External policies use the same native transcript as autonomous turns.
            self._mark_inflight([call])
            self._structured_history.append(
                {
                    "type": "function_call",
                    "call_id": call.call_id,
                    "name": call.name,
                    "arguments": json.dumps(call.arguments),
                }
            )
            self._in_turn = True
            outcome = None
            try:
                outcome = await self._step(call)
                return outcome
            finally:
                self._append_output(
                    call,
                    outcome
                    or Outcome(
                        status="cancelled",
                        reason="turn_interrupted",
                        content="Interrupted; inspect workspace before assuming side effects completed.",
                    ),
                )
                self._structured_history.extend(self._pending_messages)
                self._pending_messages.clear()
                self._in_turn = False
                self.checkpoint()
                self.evidence.path("runs", self.state.run_id, "inflight.json").unlink(missing_ok=True)

    def _take_step(self, *, detached=False):
        state = self.state
        assert state is not None
        if state.terminal:
            return Outcome(status="blocked", reason="run_terminal")
        if self._handoff_reason:
            return Outcome(status="blocked", reason="handoff_only")
        if state.step >= state.task.max_steps:
            if not self._managed_worker and not detached:
                self.finish("step_limit")
            return Outcome(status="blocked", reason="step_limit")
        reason = self._team.budget.admit(self, "tool_calls")
        if reason:
            if self._managed_worker:
                self._handoff_reason = reason
            elif not detached:
                self.finish(reason)
            return Outcome(status="blocked", reason=reason)
        state.step += 1

    async def _step(self, call: ToolCall, arguments=None) -> Outcome:
        if self.state.user_inbox:
            return Outcome(status="blocked", reason="user_message_pending",
                           content="New user guidance arrived. This tool did not run; read the guidance before deciding the next action.")
        blocked = self._take_step()
        if blocked:
            return blocked
        outcome = await self._execute(call, arguments)
        return self._collect_step(call, outcome, self.state.step)

    async def _execute_batch(self, calls: list[ToolCall], *, on_result=None) -> list[Outcome]:
        """Preflight in order, overlap detached reads, then publish results in order.

        The preparation/execution/collection split follows pi agent-loop.ts at
        46c9de402bddf46b03c3b9f46487b777aaa41861; Aster permits only read-only batches.
        """

        def record(call, outcome):
            if on_result is not None:
                on_result(call, outcome)
            return outcome

        if self._handoff_reason:
            return [record(call, Outcome(status="blocked", reason="handoff_only")) for call in calls]

        # Provider parsing has already established complete JSON and call IDs.
        # A tool's business-schema mistake is correctable feedback, not damaged
        # history. Reject the whole batch before any effect, and pair every call.
        await self.mcp.restore([call.name for call in calls])
        arguments = {call.call_id: validate_arguments(call, schemas=self.mcp.schemas) for call in calls}
        invalid = {key: value for key, value in arguments.items() if isinstance(value, Outcome)}
        if invalid:
            state = self.state
            outcomes = []
            for call in calls:
                blocked = self._take_step()
                if blocked:
                    outcome = blocked
                else:
                    outcome = self._collect_step(call, invalid.get(call.call_id) or Outcome(
                        status="blocked", reason="batch_validation_failed",
                        content="No tools in this batch ran. Correct the invalid tool arguments, then resubmit the intended calls.",
                    ), state.step)
                outcomes.append(record(call, outcome))
            return outcomes

        if (
            self.settings.read_parallelism == 1
            or len(calls) < 2
            or any(call.name not in {"read_file", "search", "list_files"} for call in calls)
        ):
            return [record(call, await self._step(call, arguments[call.call_id])
                           if self._in_turn else await self.step(call)) for call in calls]
        state = self.state
        assert state is not None
        preparations = []
        for call in calls:
            blocked = self._take_step(detached=True)
            if blocked:
                preparations.append((call, blocked, None))
            else:
                preparations.append(
                    (call, await self._prepare_tool(call, detached=True, arguments=arguments[call.call_id]), state.step)
                )

        # A stop during any preflight prevents the whole prepared batch starting.
        if state.terminal:
            preparations = [
                (
                    call,
                    prepared
                    if isinstance(prepared, Outcome)
                    else Outcome(status="blocked", reason="run_terminal"),
                    step,
                )
                for call, prepared, step in preparations
            ]
        completed = iter(await execute_reads(
            [prepared for _, prepared, _ in preparations if not isinstance(prepared, Outcome)],
            self.settings.read_parallelism,
        ))
        outcomes = []
        for call, prepared, step in preparations:
            if step is None:
                if state.terminal:
                    prepared = Outcome(status="blocked", reason="run_terminal")
                elif prepared.reason in {"step_limit", "team_tool_calls_limit"} and not self._managed_worker:
                    self.finish(prepared.reason)
                outcomes.append(record(call, prepared))
                continue
            if isinstance(prepared, Outcome):
                outcome = prepared
            else:
                result = next(completed)
                if isinstance(result, BaseException):
                    outcome = Outcome(status="error", reason=type(result).__name__)
                    duration = 0
                else:
                    outcome, duration = result
                outcome = self._finish_tool(prepared[0], outcome, duration)
            outcomes.append(record(call, self._collect_step(call, outcome, step)))
        return outcomes

    def _collect_step(self, call: ToolCall, outcome: Outcome, step: int) -> Outcome:
        state = self.state
        assert state is not None
        if self.settings.reuse_environment and (
            self._environment_generation != self.sandbox.environment_generation
        ):
            state.verification = Verification()
            state.working_memory.pop("__run_tests__", None)
        self._environment_generation = self.sandbox.environment_generation
        self.verifier.observe(state, outcome)
        state.messages.append(
            {
                "role": "tool",
                "content": outcome.content,
                "important": outcome.status != "success",
                "native": self._in_turn,
            }
        )
        if outcome.status == "success" and call.name in {
            "read_file",
            "search",
            "list_files",
            "git_diff",
        }:
            state.observed = True
        if outcome.status == "success" and call.name in self.mcp.tools:
            state.observed = True
        if outcome.status == "success" and call.name == "read_file":
            path = call.arguments["path"]
            self.memory.remember_file(
                path, self.evidence.redactor.text(outcome.content), state.working_memory
            )
            state.read_evidence[path] = outcome.data["source_sha256"]
            if self._managed_worker:
                state.worker_reads[outcome.evidence_id] = state.run_id
                # Reuse the bounded, snapshot-pinned source text already kept in working memory.
                state.working_memory[path].update(
                    start=str(outcome.data["start"]), evidence_id=outcome.evidence_id,
                    characters=str(len(self.evidence.redactor.text(outcome.content))),
                )
        elif (outcome.status == "success" and call.name in {"search", "run_tests"}
              and not outcome.data.get("idempotency_replayed")):
            self.memory.remember_file(f"__{call.name}__", outcome.content, state.working_memory)
        if outcome.status == "success" and call.name == "memory_retrieve":
            state.recalled_ids = sorted(
                set(state.recalled_ids) | {x["id"] for x in outcome.data.get("lessons", [])}
            )
        if (
            state.recalled_ids
            and call.name == "run_tests"
            and not outcome.data.get("idempotency_replayed")
            and (outcome.status == "success" or outcome.reason == "tests_failed")
        ):
            applied = set(call.arguments.get("memory_ids", [])) & set(state.recalled_ids)
            if applied:
                self.memory.record_use(applied, success=outcome.status == "success")
                state.recalled_ids = sorted(set(state.recalled_ids) - applied)
        if outcome.changed_paths:
            state.changed_paths = sorted(set(state.changed_paths + outcome.changed_paths))
            for path in outcome.changed_paths:
                # A write invalidates any earlier file fact; the next read must refresh it.
                state.working_memory.pop(path, None)
            state.working_memory.pop("__search__", None)
            state.working_memory.pop("__run_tests__", None)
        self._refresh_observations()
        if (call.name == "worker_finish" and outcome.status == "success"
                and not outcome.data.get("stop_requested")):
            if outcome.data["source_excerpts"]:
                state.observed = True
            self.finalize(outcome.data["answer"])
            if state.phase == "completed":
                state.handoff_sources = outcome.data["source_excerpts"]
            else:
                outcome.status, outcome.reason = "blocked", "worker_completion_pending"
                outcome.content = state.feedback or state.stop_reason
        reason = self.stall.observe(call, outcome)
        trace_outcome = outcome.model_dump()
        if trace_outcome.get("data", {}).get("image_data"):
            trace_outcome["data"]["image_data"] = "[stored image omitted from trace]"
        self.evidence.emit(
            state.run_id,
            "tool_result",
            step=step,
            call=call.model_dump(),
            outcome=trace_outcome,
            stall_reason=reason,
        )
        stop_requested = outcome.data.pop("stop_requested", "")
        if stop_requested:
            self.finish(stop_requested)
        elif reason:
            self.evidence.emit(state.run_id, "stall_detected", reason=reason)
            if self._recovery_used < self.settings.max_recovery:
                self._recovery_used += 1
                self.stall.items.clear()
                state.feedback = (
                    f"Recovery required ({reason}). Change approach and use fresh evidence."
                )
                self.add_message("user", state.feedback, important=True)
            else:
                self.finish("stall_recovery_exhausted")
        if not state.terminal and not outcome.data.get("idempotency_replayed"):
            self._reflect_repair(call, outcome)
        self.checkpoint()
        return outcome

    def _reflect_repair(self, call: ToolCall, outcome: Outcome):
        """One optional learning opportunity after verification, within the normal loop."""
        if (not self.memory.enabled or not self.memory.evolution or self._managed_worker
                or (self.maintenance.enabled and self.maintenance.can_generate)
                or call.name != "run_tests" or outcome.status != "success"
                or self.state.verification.state != "passed"):
            return
        rows = self.evidence.events(self.state.run_id)
        if any(row["event"] == "memory_reflection" for row in rows):
            return
        failures = [row for row in rows if row["event"] == "tool_result"
                    and row["call"]["name"] in {"run_tests", "run_shell"}
                    and row["outcome"]["status"] == "error"
                    and row["outcome"].get("exit_code") not in {None, 0}
                    and row["outcome"]["reason"] != "no_tests_collected"][-3:]
        if not failures:
            return
        references = [row["outcome"]["evidence_id"] for row in failures]
        feedback = (
            "Verification passed after earlier execution failures. If a reusable cause and "
            "fix are established, save at most one scoped procedure via memory_store "
            "(tool_load); update an existing lesson if appropriate. Describe the failure, "
            "cause, remedy and when it applies; cite the relevant failure and passing "
            "verification. Do not assume an unrelated passing test proves a repair. "
            "Skip incidental typos, transient failures and lessons already covered. "
            "If saving, briefly tell the user what went wrong, what fixed it, and the "
            "actual storage outcome; claim saved only after tool success. "
            f"Failure evidence IDs: {', '.join(references)}. "
            f"Passing evidence ID: {outcome.evidence_id}."
        )
        self.evidence.emit(self.state.run_id, "memory_reflection",
                           failures=references, verification=outcome.evidence_id)
        self.add_message("user", feedback, important=True)

    def _refresh_observations(self):
        """Drop stale read summaries even when another writer edited an already-touched path."""
        state = self.state
        assert state is not None
        stale = []
        # ponytail: reread observed text at step/turn boundaries; use a file watcher only
        # if the local I/O cost becomes material for repositories beyond this harness's scale.
        for path, expected in state.read_evidence.items():
            try:
                current = hashlib.sha256(self.workspace.read(path).encode()).hexdigest()
            except (OSError, ValueError, UnicodeError):
                current = None
            if current != expected:
                stale.append(path)
        for path in stale:
            state.read_evidence.pop(path)
            state.working_memory.pop(path, None)
        verification_stale = state.verification.state == "passed" and (
            state.verification.workspace_hash != self.workspace.fingerprint()
            or (
                self.settings.reuse_environment
                and self._environment_generation != self.sandbox.environment_generation
            )
        )
        if verification_stale:
            state.verification.state = "missing"
        self._environment_generation = self.sandbox.environment_generation
        if stale or verification_stale:
            state.working_memory.pop("__search__", None)
            state.working_memory.pop("__run_tests__", None)
            self.evidence.emit(
                state.run_id,
                "observations_invalidated",
                paths=stale,
                verification_stale=verification_stale,
            )

    async def _execute(self, call: ToolCall, arguments=None) -> Outcome:
        await self.mcp.restore([call.name])
        # Earlier tools in a batch may refresh and withdraw or change MCP schemas.
        if arguments is None or call.name.startswith("mcp__"):
            arguments = validate_arguments(call, schemas=self.mcp.schemas)
        if isinstance(arguments, Outcome):
            return arguments
        call = call.model_copy(update={"arguments": arguments.model_dump()})
        journal = None
        if self.mcp.effect(call.name) in {"write", "process"}:
            if reason := self._unavailable_reason(call.name):
                return Outcome(status="blocked", reason=reason)
            journal = OperationJournal(self.evidence, self.state.session_id, call,
                                       self._safety_identity(self.settings.identity()))
            try:
                replay = journal.lookup()
            except (OSError, ValueError, KeyError, TypeError):
                return Outcome(status="blocked", reason="idempotency_journal_unavailable")
            if replay is not None:
                return self._finish_tool(call, replay, 0) if replay.data.get("idempotency_replayed") else replay
        prepared = await self._prepare_tool(call, arguments=arguments)
        if isinstance(prepared, Outcome):
            return prepared
        call, registry = prepared
        started = time.monotonic()
        mcp_write = call.name in self.mcp.tools and self.mcp.effect(call.name) == "write"
        before = self.workspace.snapshot() if mcp_write else None
        if journal:
            try:
                journal.reserve()
            except OSError:
                return Outcome(status="blocked", reason="idempotency_journal_unavailable")
        outcome = await registry.execute(call)
        if before is not None:
            outcome.changed_paths = Workspace.changes(before, self.workspace.snapshot())
            outcome.partial_success = bool(outcome.changed_paths and outcome.status != "success")
            self.state.verification = Verification()
        if journal:
            try:
                journal.settle(outcome)
            except OSError:
                outcome.data.update(stop_requested="idempotency_journal_unavailable", uncertain=True)
                outcome.status, outcome.reason = "error", "idempotency_journal_unavailable"
        return self._finish_tool(call, outcome, (time.monotonic() - started) * 1000)

    async def _prepare_tool(self, call: ToolCall, *, detached=False, arguments=None):
        state = self.state
        assert state is not None
        await self.mcp.restore([call.name])
        if arguments is None or call.name.startswith("mcp__"):
            arguments = validate_arguments(call, schemas=self.mcp.schemas)
        if isinstance(arguments, Outcome):
            return arguments
        effect = self.mcp.effect(call.name)
        if effect in {"write", "process"} and self._team.writer_busy(self):
            return Outcome(status="blocked", reason="worker_writer_active",
                           content="Await or stop the writing worker before editing or running processes.")
        call = call.model_copy(update={"arguments": arguments.model_dump()})
        unavailable = self._unavailable_reason(call.name)
        if unavailable:
            return Outcome(status="blocked", reason=unavailable)
        expected_files = None
        if call.name in {"write_file", "patch_file"}:
            path = call.arguments.get("path", "")
            try:
                target = self.workspace.path(path, write=True)
                current, fingerprint = read_edit_source(target) if target.exists() else (None, None)
            except (OSError, ValueError) as exc:
                return Outcome(status="blocked", reason=type(exc).__name__, content=str(exc))
            if current is not None:
                if state.read_evidence.get(path) != hashlib.sha256(current.encode()).hexdigest():
                    return Outcome(
                        status="blocked",
                        reason="fresh_read_required",
                        content=(
                            f"Call read_file on {path} before retrying. Every successful edit "
                            "invalidates the previous read, including edits in the same batch. "
                            "Combine planned changes in one write_file or patch_file when possible."
                        ),
                    )
            # Raw bytes preserve newline-only edits too; None means create-only.
            expected_files = {str(target.relative_to(self.workspace.root)): fingerprint}
        decision = permission(call.name, effect, call.arguments, self.settings, state.mode)
        self.evidence.emit(
            state.run_id,
            "permission_decision",
            call=call.model_dump(),
            decision=decision.action,
            reason=decision.reason,
        )
        if decision.action == "deny":
            return Outcome(status="blocked", reason=decision.reason)
        if decision.action == "ask":
            if self.approval_callback is None:
                return Outcome(status="blocked", reason="approval_unavailable")
            approved = self.approval_callback(call.model_copy(deep=True), decision)
            if inspect.isawaitable(approved):
                approved = await approved
            if not approved:
                return Outcome(status="blocked", reason="approval_denied")
        if state.terminal:
            return Outcome(status="blocked", reason="run_terminal")
        if state.user_inbox:
            return Outcome(status="blocked", reason="user_message_pending",
                           content="New user guidance arrived before execution; this tool did not run.")
        before = self.hooks.before_tool(state, call.model_copy(deep=True))
        if before.action == "stop":
            self.finish("hook_stop")
            return Outcome(status="blocked", reason=before.feedback or "hook_stop")
        if before.action == "deny":
            return Outcome(status="blocked", reason=before.feedback or "hook_denied")
        context = ToolContext(
            self.workspace, self.sandbox, state.run_id, expected_files=expected_files,
            readonly=state.mode == "plan", workflow=None if detached else self._workflow_tools(),
            mcp=self.mcp,
        )
        return call, ToolRegistry(context, validated=(call, arguments))

    def _finish_tool(self, call: ToolCall, outcome: Outcome, duration_ms: float) -> Outcome:
        state = self.state
        assert state is not None
        if call.name == "mcp_list" or call.name in self.mcp.tools:
            outcome.content = self.evidence.redactor.text(outcome.content)
        if (call.name in self.mcp.tools
                or (call.name in {"run_shell", "run_tests"} and "captured_bytes" in outcome.data)):
            outcome.content = self.evidence.redactor.text(outcome.content)
            ref = None
            try:
                ref = self.evidence.archive_observation(
                    state.run_id, call.call_id, call.name, outcome.content
                )
            except (OSError, ValueError):
                # Execution already happened: preserve its facts even if storage failed.
                outcome.data["execution_status"] = outcome.status
                outcome.data["stop_requested"] = "output_archive_unavailable"
                outcome.status, outcome.reason = "error", "output_archive_unavailable"
                outcome.partial_success = bool(outcome.changed_paths)
            if ref:
                outcome.data["observation"] = {"id": ref["id"], "characters": ref["characters"]}
            if ref and len(outcome.content) > 8192:
                outcome.content = outcome.content[:8192] + (
                    f"\n[Preview clipped; observation_read id={ref['id']} "
                    f"has {ref['characters']} captured characters.]"
                )
            if outcome.data.get("output_truncated"):
                outcome.content += (
                    f"\n[Output exceeded {outcome.data['output_limit_bytes']} bytes; "
                    f"captured {outcome.data['captured_bytes']} of {outcome.data['output_bytes']} "
                    "bytes. Overflow is unavailable; narrow or redirect the command output.]"
                )
        outcome.duration_ms = duration_ms
        outcome.evidence_id = self.evidence.emit(
            state.run_id,
            "tool_decision",
            call_id=call.call_id,
            tool=call.name,
            status=outcome.status,
            reason=outcome.reason,
        )["hash"]
        if outcome.data.get("idempotency_replayed"):
            state.used_call_ids.append(call.call_id)
            return outcome
        if call.name == "run_tests":
            outcome.data["is_verification"] = True
            outcome.data["command"] = call.arguments.get("command", "")
        after = (
            self.hooks.after_tool(state, call, outcome)
            if outcome.status == "success"
            else self.hooks.after_failure(state, call, outcome)
        )
        if after.feedback:
            self.add_message("user", after.feedback, important=True)
        if after.action == "stop":
            outcome.data["stop_requested"] = "hook_stop"
        state.used_call_ids.append(call.call_id)
        return outcome

    def _workflow_tools(self):
        return WorkflowTools(
            self.state, self.workspace, self.memory, self.skills, self.evidence,
            self._archive_sources, self._initial_text, self._initial_modes,
            self._unavailable_reason, self._settle_workers, self.workers.close,
            self.workers.start, self.workers.status, self.workers.message, self.workers.stop,
            self.question_callback,
            self.mcp.schemas,
        )

    async def workflow_tool(self, name: str, args: dict) -> Outcome:
        """Compatibility entry for existing direct workflow callers."""
        return await self._workflow_tools().execute(name, args)

    def finalize(self, answer: str) -> RunState:
        state = self.state
        assert state is not None
        if state.terminal:
            return state
        if self._operation_task is not None and self._operation_task is not asyncio.current_task():
            raise RuntimeError("runtime_busy: await the current operation before Final")
        if state.user_inbox:
            self._deliver_user_messages()
            self.checkpoint()
            return state
        delivered = self.workers.deliver()
        if (any(not worker.future.done() for worker in self.workers.workers.values())
                or any(item.get("delivery") == "running" for item in state.pending_requirements)):
            state.feedback = (
                "Worker requirement(s) are still running. Await them with "
                "worker_status(wait_seconds=300) or explicitly stop them, then review "
                "their artifacts before final delivery."
            )
            self.add_message("user", state.feedback, important=True)
            self.evidence.emit(state.run_id, "requirement_wait", feedback=state.feedback)
            return state
        if delivered:
            state.feedback = "New worker handoffs arrived after your last observation. Review them before final delivery."
            self.add_message("user", state.feedback, important=True)
            return state
        self._settle_workers()
        self._refresh_observations()
        feedback = completion_feedback(state) if self.settings.completion != "off" else ""
        if feedback and state.final_revisions < self.settings.max_final_revisions:
            state.final_revisions += 1
            state.feedback = feedback
            self.add_message("user", feedback, important=True)
            self.evidence.emit(state.run_id, "completion_revision", feedback=feedback)
            return state
        result = self.verifier.final(state)
        self.evidence.emit(state.run_id, "verifier_result", result=result.model_dump())
        if (not result.passed and self.settings.completion == "strict"
                and result.reason != "requirements_cancelled"):
            state.feedback = result.reason
            self.finish("completion_requirements_failed")
            return state
        if result.reason == "requirements_cancelled":
            answer += "\n\nCancelled requirements (not verified):\n" + "\n".join(
                f"- {item['content']}: {item.get('resolution', '')}"
                for item in state.cancelled_requirements
            )
        answer = self._memory_confirmation(answer)
        state.final = self.evidence.redactor.text(answer)
        state.phase = "completed"
        state.stop_reason = "verified" if result.passed else "completed_with_warning"
        self.evidence.emit(state.run_id, "run_completed", result=result.model_dump(), answer=answer)
        self._stop_hook()
        self._auto_dream()
        self.report(result)
        return state

    def _memory_confirmation(self, answer):
        """Correct common save claims using committed events, without another model call."""
        claims_save = re.search(
            r"已(?:经)?(?:记住|沉淀)|已(?:经)?(?:保存|记录).{0,24}(?:记忆|规则|约定|经验)"
            r"|\b(?:saved|stored|recorded|remembered)\b.{0,48}\b(?:memory|rule|convention|lesson)\b",
            answer, re.IGNORECASE,
        )
        if not claims_save:
            return answer
        events = self.evidence.events(self.state.run_id)
        saved = any(
            row["event"] == "tool_result" and row["call"]["name"] == "memory_store"
            and row["outcome"]["status"] == "success" for row in events
        ) or any(row["event"] == "memory_maintenance" and row.get("receipts") for row in events)
        if not saved:
            answer += "\n\n记忆保存状态：本次尚未写入长期记忆；如后台整理后成功保存，会另行显示记录内容。"
        return answer

    def finish(self, reason: str, *, failed: bool = False):
        if not self.state or self.state.terminal:
            return
        if self._operation_task is not None and self._operation_task is not asyncio.current_task():
            if not self._operation_task.cancelling():
                self._operation_task.cancel()
        self._settle_workers()
        self.state.phase = "failed" if failed else "stopped"
        self.state.stop_reason = reason
        self._stop_hook()
        self.evidence.emit(self.state.run_id, "run_stopped", reason=reason, failed=failed)
        self._auto_dream()
        self.report(VerifierResult(passed=False, reason=reason))

    def _settle_workers(self):
        """Freeze children and account for their edits even without a status poll."""
        self.workers.cancel()
        for worker in self.workers.workers.values():
            child = worker.runtime.state
            if child.parent_run_id == self.state.run_id:
                self.state.changed_paths = sorted(
                    set(self.state.changed_paths + child.changed_paths)
                )
        self._refresh_observations()

    def _stop_hook(self):
        if not self._finish_hook_ran:
            self._finish_hook_ran = True
            hook = self.hooks.stop(self.state)
            if hook.feedback:
                self.state.feedback = hook.feedback
            self.evidence.emit(self.state.run_id, "stop_hook", feedback=hook.feedback)

    def abort(self, reason: str = "user_abort"):
        """Turn-level cancellation point used by CLI integrations and RL truncation."""
        self.finish(reason)

    def report(self, result: VerifierResult):
        if not self.state:
            return
        self.checkpoint()
        self.evidence.save(
            ("runs", self.state.run_id, "report.json"),
            {
                "run_id": self.state.run_id,
                "phase": self.state.phase,
                "stop_reason": self.state.stop_reason,
                "verifier": result.model_dump(),
                "steps": self.state.step,
                "turns": self.state.turn,
                "changed_paths": self.state.changed_paths,
                "verification": self.state.verification.model_dump(),
                "usage": self.state.usage,
                "memory_usage": self.state.memory_usage,
                "team_usage": self.state.team_usage,
            },
        )

    def enqueue_user_message(self, content: str) -> str:
        """Accept guidance while running; delivery only happens at a safe tool boundary."""
        state = self.state
        if state is None or state.terminal:
            raise ValueError("当前任务已停止，请直接发送消息继续会话。")
        content = content.strip()
        if not content or len(content) > 16384:
            raise ValueError("补充消息需要 1–16384 个字符。")
        if len(state.user_inbox) >= 16:
            raise ValueError("已有 16 条补充消息待处理，请等待当前操作结束。")
        message = {"id": uuid4().hex, "content": content}
        queued = [*state.user_inbox, message]
        # Separate mailbox survives a crash while a tool batch cannot yet be checkpointed.
        parts = ("runs", state.run_id, "user-inbox.json")
        mailbox = self.evidence.path(*parts)
        recorded = json.loads(mailbox.read_text()) if mailbox.exists() else []
        self.evidence.save(parts, [*recorded, message])
        state.user_inbox = queued
        self.evidence.emit(state.run_id, "user_message_queued", message=message)
        return message["id"]

    def _deliver_user_messages(self):
        state = self.state
        if not state or not state.user_inbox or self._in_turn:
            return
        queued, state.user_inbox = state.user_inbox, []
        for message in queued:
            self.add_message("user", message["content"], important=True)
            state.delivered_user_messages.append(message["id"])
            for name, required in self.verifier.infer(message["content"]).items():
                if required:
                    setattr(state.task, name, True)
            self.evidence.emit(state.run_id, "user_message_delivered", message_id=message["id"])
        state.verification = Verification()
        state.read_evidence.clear()
        state.working_memory.clear()
        state.feedback = "New user guidance received; address it while continuing the task."
        self.stall.items.clear()

    def add_message(self, role: str, content: str, *, important=False, native=False):
        if self.state:
            self.state.messages.append(
                {"role": role, "content": content, "important": important, "native": native}
            )
            if role == "user":
                target = self._pending_messages if self._in_turn else self._structured_history
                target.append({"role": "user", "content": content})

    def _memory_text(self, query: str) -> str:
        if not self.settings.memory_enabled:
            return ""
        latest = next((row.get("feedback") or row.get("task", {}).get("prompt", "")
                       for row in reversed(self.evidence.events(self.state.run_id))
                       if row["event"] in {"run_started", "run_continued"}), "") if self.state else ""
        query = query + "\n" + latest
        lessons = self.memory.retrieve(query, limit=3)
        if self.state:
            self.state.recalled_ids = sorted(
                set(self.state.recalled_ids) | {x["id"] for x in lessons}
            )
        if self.state:
            self.evidence.emit(
                self.state.run_id, "memory_recalled", count=len(lessons),
                ids=[item["id"] for item in lessons],
            )
        return "\n".join(
            f"- [{x['id']}; {x.get('kind', 'excerpt')}; {x.get('when') or 'project excerpt'}] "
            f"{x['text']} (evidence: {x['provenance'].get('evidence_id', '')})" for x in lessons
        )

    def _unavailable_reason(self, name, *, state=None):
        state = self.state if state is None else state
        if name not in self.mcp.schemas:
            return "unknown_tool"
        if name in self.mcp.tools:
            server, original = self.mcp.tools[name]
            cfg = self.settings.mcp_servers.get(server)
            if not cfg or not cfg.enabled or (cfg.enabled_tools is not None and original not in cfg.enabled_tools):
                return "mcp_tool_disabled"
            if cfg.url and not self.settings.network:
                return "network_disabled"
            if self.mcp.effect(name) == "write":
                if self.settings.read_only:
                    return "read_only"
                if state and state.mode == "plan":
                    return "plan_mode"
                if self.settings.write_scope is not None:
                    return "mcp_write_scope_unenforceable"
                if self.settings.approval == "never" or (
                    self.settings.approval == "ask" and self.approval_callback is None
                ):
                    return "approval_unavailable"
        return unavailable_reason(
            name, self.settings, self.state if state is None else state,
            approval_available=self.approval_callback is not None,
            question_available=self.question_callback is not None,
        )

    def _tool_names(self, *, state=None):
        state = self.state if state is None else state
        selected = (
            self.mcp.schemas
            if self.settings.tool_profile == "all"
            else (set(CORE_TOOLS) | set(state.loaded_tools if state else []))
        )
        if "mcp_list" in self.mcp.schemas:
            selected = set(selected) | {"mcp_list"}
        if self.settings.max_workers > 0:
            selected = set(selected) | {"worker_start"}
        if state and state.parent_run_id:
            selected = set(selected) | {"worker_finish"}
        if state and state.worker_sessions:
            selected = set(selected) | {"worker_status"}
        return sorted(name for name in selected if not self._unavailable_reason(name, state=state))

    def tool_definitions(self, *, state=None):
        available = [name for name in self.mcp.schemas if not self._unavailable_reason(name, state=state)]
        return definitions(self._tool_names(state=state), available=available, schemas=self.mcp.schemas)

    def _request_tool_definitions(self, settings, *, state=None):
        # Parent delivery keeps the cacheable catalog; _execute_batch still denies all calls.
        if self._handoff_reason and self._managed_worker:
            return []
        tools = self.tool_definitions(state=state)
        if self.evidence.redactor.value(tools, mask_fields=False) != tools:
            raise ContextHistoryError("tool_definitions_contain_credential")
        if self.evidence.redactor.text(settings.model) != settings.model:
            raise ContextHistoryError("model_name_contains_credential")
        return tools

    def _auto_dream(self):
        if self._dream_ran or not self.settings.auto_dream:
            return
        self._dream_ran = True
        try:
            result = self.memory.dream()
            if self.state:
                self.evidence.emit(self.state.run_id, "memory_dream", **result)
                self.maintenance.enqueue()
        except Exception as exc:
            if self.state:
                self.evidence.emit(
                    self.state.run_id, "memory_dream", status="failed", error=type(exc).__name__
                )
