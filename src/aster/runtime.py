"""One transition engine used by autonomous runs, CLI and the RL adapter."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import shlex
import time
from collections.abc import Awaitable, Callable
from contextlib import contextmanager
from pathlib import Path

from .config import Settings
from .context import ContextBudgetError, ContextBuilder, ContextHistoryError
from .evidence import EvidenceStore, Redactor
from .hooks import Hooks
from .memory import Memory
from .provider import ModelClient, ProviderError, history_groups, request_payload
from .recovery import StallDetector, completion_feedback
from .safety.policy import Decision, permission
from .safety.runner import SandboxRunner
from .schema import Outcome, RunState, TaskSpec, ToolCall, VerifierResult
from .skills import Skills
from .tools import (
    CORE_TOOLS,
    SCHEMAS,
    TEST_COMMAND_PREFIXES,
    ToolContext,
    ToolRegistry,
    definitions,
    text_page,
)
from .verifier import Verifier
from .workers import WorkerManager
from .workspace import Workspace


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
    ):
        self.workspace = Workspace(root)
        self.settings = settings
        self.approval_callback = approval_callback
        self.question_callback = question_callback
        secrets = [settings.api_key.get_secret_value()]
        self.evidence = EvidenceStore(self.workspace.root, Redactor(secrets))
        self.memory = Memory(
            self.workspace.root, settings.memory_enabled, settings.memory_evolution, secrets=secrets
        )
        self.model = model or ModelClient(settings)
        self.sandbox = SandboxRunner(settings)
        self.skills = Skills(self.workspace.root)
        self.context = ContextBuilder(settings.effective_context_tokens, settings.output_tokens)
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
        self.workers = WorkerManager(self)

    @contextmanager
    def _operation(self):
        """One task owns transitions; run -> turn -> step may nest in that task."""
        task = asyncio.current_task()
        if self._operation_task is not None and self._operation_task is not task:
            raise RuntimeError("runtime_busy: await the current operation")
        owner = self._operation_task is None
        if owner:
            self._operation_task = task
        try:
            yield
        except asyncio.CancelledError:
            self.finish("cancelled")
            raise
        finally:
            if owner:
                self._operation_task = None
                self.checkpoint()

    def _ensure_idle(self):
        if self._operation_task or any(not w.future.done() for w in self.workers.workers.values()):
            raise RuntimeError("runtime_busy: await current operations and workers")

    def start(self, task: TaskSpec) -> RunState:
        self._ensure_idle()
        if self.state and not self.state.terminal:
            self.finish("superseded")
        task = task.model_copy(deep=True)
        inferred = self.verifier.infer(task.prompt)
        for key, value in inferred.items():
            if key not in task.model_fields_set:
                setattr(task, key, value)
        self.state = RunState(task=task, phase="running")
        self.workers = WorkerManager(self)
        self._initial_text = self.workspace.text_snapshot()
        self._initial_modes = self.workspace.mode_snapshot()
        self.stall.items.clear()
        self._recovery_used = 0
        # The first turn is rebuilt by ContextBuilder so system policy and untrusted-data
        # boundaries are actually sent to the model; this seed is only for the task state.
        self._structured_history = []
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
        if not self.state or self._in_turn:
            return
        history_groups(self._structured_history)
        self.evidence.save(
            ("runs", self.state.run_id, "checkpoint.json"),
            {
                "state": self.state.model_dump(mode="json"),
                "workspace_hash": self.workspace.fingerprint(),
                "workspace": self.workspace.snapshot(),
                "runtime_identity": self.settings.identity(),
                "structured_history": self._structured_history,
                "initial_text": self._initial_text,
                "initial_modes": self._initial_modes,
                "recovery_used": self._recovery_used,
                "stall_items": list(self.stall.items),
            },
        )

    def resume(self, run_id: str) -> RunState:
        self._ensure_idle()
        if self.state and not self.state.terminal:
            raise RuntimeError("active_run: finish the current run before resuming another")
        data = json.loads(self.evidence.path("runs", run_id, "checkpoint.json").read_text())
        if data["runtime_identity"] != self.settings.identity():
            raise ValueError("runtime_identity_mismatch")
        if data["workspace_hash"] != self.workspace.fingerprint():
            raise ValueError("workspace_mismatch")
        state = RunState.model_validate(data["state"])
        if not state.terminal and "initial_text" not in data:
            raise ValueError("legacy_checkpoint_missing_diff_baseline")
        history_groups(data.get("structured_history", []))
        self.state = state
        self.workers = WorkerManager(self)
        self._initial_text = data.get("initial_text", {})
        self._initial_modes = data.get("initial_modes", {})
        self._recovery_used = data.get("recovery_used", 0)
        self.stall.items.clear()
        self.stall.items.extend(tuple(item) for item in data.get("stall_items", []))
        self._finish_hook_ran = False
        self._dream_ran = False
        self._pending_messages = []
        self._structured_history = data.get("structured_history", [])
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
        if state.turn >= state.task.max_turns:
            self.finish("turn_limit")
            return state
        try:
            prompt, tool_defs, report = self.prepare_request()
        except (ContextBudgetError, ContextHistoryError) as exc:
            state.feedback = str(exc)
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
        # Settings are mutable; the counted request must use the provider's current model limits.
        self.context.budget = self.settings.effective_context_tokens
        self.context.output_reserve = self.settings.output_tokens
        self._refresh_observations()
        memory_text = self._memory_text(state.task.prompt)
        working_memory = self.memory.working_section(state.working_memory)
        skills = "\n".join(self.skills.list())
        skill_bodies = {}
        for name in dict.fromkeys(state.active_skills):
            try:
                skill_bodies[name] = self.skills.load(name)
            except (OSError, ValueError):
                skill_bodies[name] = (
                    "[Skill unavailable; check the skill catalog before reloading.]"
                )
        tool_defs = self.tool_definitions()

        def validate_archive(ref):
            # The archive id already hashes provenance and body; read verifies both.
            self.evidence.read_observation(state.run_id, ref["id"], limit=1)

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
            skill_bodies=skill_bodies,
            archive=lambda call_id, tool, content: self.evidence.archive_observation(
                state.run_id, call_id, tool, content
            ),
            validate_archive=validate_archive,
        )
        payload = request_payload(
            prompt,
            tool_defs,
            protocol=self.settings.protocol,
            model=self.settings.model,
            output_tokens=self.settings.output_tokens,
        )
        # Keep the actual model observation apart from the full execution trace.
        request_hash = hashlib.sha256(
            json.dumps(
                self.evidence.redactor.value(payload, mask_fields=False),
                sort_keys=True,
                ensure_ascii=False,
            ).encode()
        ).hexdigest()
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

    async def _model_turn(self, prompt, tool_defs):
        state = self.state
        assert state is not None
        try:
            request_messages = prompt
            reply = await self.model.complete(request_messages, tool_defs)
        except ProviderError as exc:
            state.feedback = str(exc)
            for name, value in exc.details.get("usage", {}).items():
                state.usage[name] = state.usage.get(name, 0) + value
            self.evidence.emit(
                state.run_id, "provider_error", error=str(exc), **getattr(exc, "details", {})
            )
            self.finish("provider_error", failed=True)
            return state
        if state.terminal:
            return state
        for name, value in reply.usage.items():
            state.usage[name] = state.usage.get(name, 0) + value
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
            self.add_message("assistant", reply.text, important=True, native=bool(reply.raw))
        self.evidence.emit(
            state.run_id,
            "model_reply",
            turn=state.turn,
            text=reply.text,
            calls=[c.model_dump() for c in reply.calls],
            usage=reply.usage,
        )
        if not reply.calls:
            if reply.text:
                return self.finalize(reply.text)
            state.feedback = "Model returned neither text nor a tool call."
            self.finish("empty_model_reply", failed=True)
            return state
        self._in_turn = True
        try:
            for call in reply.calls:
                if state.terminal:
                    outcome = Outcome(status="blocked", reason="run_terminal")
                else:
                    outcome = await self.step(call)
                self._append_output(call, outcome)
        finally:
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
            self.checkpoint()
        return state

    def _append_output(self, call: ToolCall, outcome: Outcome):
        output = json.dumps(
            {
                "status": outcome.status,
                "reason": outcome.reason,
                "evidence_id": outcome.evidence_id,
                "changed_paths": outcome.changed_paths,
                "content": self.evidence.redactor.text(outcome.content),
                **(
                    {"page": "observation" if call.name == "observation_read" else "text"}
                    if call.name in {"observation_read", "todo_list", "skill_load"}
                    and outcome.status == "success"
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

    async def _step(self, call: ToolCall) -> Outcome:
        state = self.state
        assert state is not None
        if state.terminal:
            return Outcome(status="blocked", reason="run_terminal")
        if state.step >= state.task.max_steps:
            self.finish("step_limit")
            return Outcome(status="blocked", reason="step_limit")
        state.step += 1
        outcome = await self._execute(call)
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
        if outcome.status == "success" and call.name == "read_file":
            path = call.arguments["path"]
            self.memory.remember_file(path, outcome.content, state.working_memory)
            state.read_evidence[path] = outcome.data["source_sha256"]
        elif outcome.status == "success" and call.name in {"search", "run_tests"}:
            self.memory.remember_file(f"__{call.name}__", outcome.content, state.working_memory)
        if outcome.status == "success" and call.name == "memory_retrieve":
            state.recalled_ids = sorted(
                set(state.recalled_ids) | {x["id"] for x in outcome.data.get("lessons", [])}
            )
        if (
            state.recalled_ids
            and call.name == "run_tests"
            and (outcome.status == "success" or outcome.reason == "tests_failed")
        ):
            self.memory.record_use(state.recalled_ids, success=outcome.status == "success")
            state.recalled_ids = []
        if outcome.changed_paths:
            state.changed_paths = sorted(set(state.changed_paths + outcome.changed_paths))
            for path in outcome.changed_paths:
                # A write invalidates any earlier file fact; the next read must refresh it.
                state.working_memory.pop(path, None)
            state.working_memory.pop("__search__", None)
            state.working_memory.pop("__run_tests__", None)
        self._refresh_observations()
        reason = self.stall.observe(call, outcome)
        trace_outcome = outcome.model_dump()
        if trace_outcome.get("data", {}).get("image_data"):
            trace_outcome["data"]["image_data"] = "[stored image omitted from trace]"
        self.evidence.emit(
            state.run_id,
            "tool_result",
            step=state.step,
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
        self.checkpoint()
        return outcome

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
        verification_stale = (
            state.verification.state == "passed"
            and state.verification.workspace_hash != self.workspace.fingerprint()
        )
        if verification_stale:
            state.verification.state = "missing"
        if stale or verification_stale:
            state.working_memory.pop("__search__", None)
            state.working_memory.pop("__run_tests__", None)
            self.evidence.emit(
                state.run_id,
                "observations_invalidated",
                paths=stale,
                verification_stale=verification_stale,
            )

    async def _execute(self, call: ToolCall) -> Outcome:
        state = self.state
        assert state is not None
        if call.name not in SCHEMAS:
            return Outcome(status="blocked", reason="unknown_tool")
        model, _description, effect = SCHEMAS[call.name]
        try:
            arguments = model.model_validate(call.arguments).model_dump()
        except ValueError as exc:
            return Outcome(status="blocked", reason="invalid_arguments", content=str(exc))
        call = call.model_copy(update={"arguments": arguments})
        unavailable = self._unavailable_reason(call.name)
        if unavailable:
            return Outcome(status="blocked", reason=unavailable)
        if call.name in {"write_file", "patch_file"}:
            path = call.arguments.get("path", "")
            try:
                current = self.workspace.read(path)
            except (FileNotFoundError, ValueError):
                current = None
            if current is not None:
                fingerprint = hashlib.sha256(current.encode()).hexdigest()
                if state.read_evidence.get(path) != fingerprint:
                    return Outcome(
                        status="blocked",
                        reason="fresh_read_required",
                        content=f"Read {path} immediately before editing it.",
                    )
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
            approved = self.approval_callback(call, decision)
            if inspect.isawaitable(approved):
                approved = await approved
            if not approved:
                return Outcome(status="blocked", reason="approval_denied")
        if state.terminal:
            return Outcome(status="blocked", reason="run_terminal")
        before = self.hooks.before_tool(state, call)
        if before.action == "stop":
            self.finish("hook_stop")
            return Outcome(status="blocked", reason=before.feedback or "hook_stop")
        if before.action == "deny":
            return Outcome(status="blocked", reason=before.feedback or "hook_denied")
        context = ToolContext(self.workspace, self.sandbox, state.run_id, self)
        registry = ToolRegistry(context)
        started = time.monotonic()
        outcome = await registry.execute(call)
        outcome.duration_ms = (time.monotonic() - started) * 1000
        outcome.evidence_id = self.evidence.emit(
            state.run_id,
            "tool_decision",
            call_id=call.call_id,
            tool=call.name,
            status=outcome.status,
            reason=outcome.reason,
        )["hash"]
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

    async def workflow_tool(self, name: str, args: dict) -> Outcome:
        state = self.state
        assert state is not None
        if name == "observation_read":
            try:
                if args["observation_id"]:
                    page = self.evidence.read_observation(
                        state.run_id,
                        args["observation_id"],
                        offset=args["offset"],
                        limit=args["limit"],
                        query=args["query"],
                    )
                else:
                    page = self.evidence.list_observations(
                        state.run_id,
                        offset=args["offset"],
                        limit=min(10, args["limit"]),
                        query=args["query"],
                    )
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
        if name == "todo_add":
            item = {
                "id": str(len(state.todos) + 1),
                "content": args["content"],
                "priority": args["priority"],
                "status": "pending",
            }
            state.todos.append(item)
            return Outcome(
                status="success", content=json.dumps(item, ensure_ascii=False), data=item
            )
        if name == "todo_update":
            item = next((x for x in state.todos if x["id"] == args["todo_id"]), None)
            if item is None:
                return Outcome(status="blocked", reason="todo_not_found")
            item["status"] = args["status"]
            return Outcome(
                status="success", content=json.dumps(item, ensure_ascii=False), data=item
            )
        if name == "plan_write":
            state.plan = args["content"]
            return Outcome(status="success", content="plan updated")
        if name == "todo_list":
            data = {"plan": state.plan, "todos": state.todos}
            result = text_page(
                self.evidence.redactor.text(json.dumps(data, ensure_ascii=False)),
                args["offset"],
                args["limit"],
            )
            result.data.update(data)
            return result
        if name == "tool_load":
            requested = args["names"]
            unknown = sorted(set(requested) - SCHEMAS.keys())
            if unknown:
                return Outcome(
                    status="blocked",
                    reason="unknown_tool_name",
                    content=json.dumps(unknown, ensure_ascii=False),
                )
            unavailable = {
                name: self._unavailable_reason(name)
                for name in requested
                if self._unavailable_reason(name)
            }
            if unavailable:
                return Outcome(
                    status="blocked",
                    reason="tool_unavailable",
                    content=json.dumps(unavailable, ensure_ascii=False),
                )
            state.loaded_tools = sorted(set(state.loaded_tools + requested))
            return Outcome(
                status="success",
                content=json.dumps(state.loaded_tools, ensure_ascii=False),
                data={"loaded": state.loaded_tools},
            )
        if name == "plan_enter":
            await self.workers.close()
            if state.terminal:
                return Outcome(status="blocked", reason="run_terminal")
            self._settle_workers()
            state.mode = "plan"
            return Outcome(status="success", content="plan mode enabled")
        if name == "plan_exit":
            state.mode = "work"
            return Outcome(status="success", content="plan mode disabled")
        if name == "git_diff":
            state.observed = True
            return Outcome(
                status="success",
                content=self.workspace.diff(self._initial_text, self._initial_modes),
                data={"diff": True},
            )
        if name == "memory_retrieve":
            lessons = self.memory.retrieve(args["query"])
            state.recalled_ids = sorted(set(state.recalled_ids) | {x["id"] for x in lessons})
            return Outcome(
                status="success",
                content=json.dumps(lessons, ensure_ascii=False),
                data={"lessons": lessons},
            )
        if name in {"memory_store", "memory_update"}:
            events = self.evidence.events(state.run_id)
            evidence = next(
                (
                    e
                    for e in events
                    if e["event"] == "tool_result"
                    and (
                        e["hash"] == args["evidence_id"]
                        or e.get("outcome", {}).get("evidence_id") == args["evidence_id"]
                    )
                    and e["outcome"]["status"] == "success"
                ),
                None,
            )
            if evidence is None or evidence["call"]["name"] != "read_file":
                return Outcome(status="blocked", reason="memory_provenance")
            if name == "memory_update" and not any(
                x["id"] == args["lesson_id"] for x in self.memory.state["lessons"]
            ):
                return Outcome(status="blocked", reason="memory_not_found")
            observed = evidence["outcome"]
            data = observed.get("data", {})
            if not data.get("source_sha256") or not data.get("path"):
                return Outcome(status="blocked", reason="memory_provenance")
            sources = {data["path"]: data["source_sha256"]} if data.get("source_sha256") else {}
            lesson = self.memory.record(
                args["text"],
                run_id=state.run_id,
                evidence_id=args["evidence_id"],
                tags=args.get("tags", []),
                observation=observed["content"],
                sources=sources,
            )
            if lesson and name == "memory_update" and lesson["id"] != args["lesson_id"]:
                self.memory.discard(args["lesson_id"], reason="superseded")
            return Outcome(
                status="success" if lesson else "blocked",
                reason="" if lesson else self.memory.last_rejection,
                content=json.dumps(lesson or {}, ensure_ascii=False),
                data=lesson or {},
            )
        if name == "memory_discard":
            found = self.memory.discard(args["lesson_id"])
            return Outcome(
                status="success" if found else "blocked",
                reason="" if found else "memory_not_found",
                content="lesson discarded" if found else "",
            )
        if name == "skill_load":
            # Page offsets refer to the same redacted text later compacted for the model.
            text = self.evidence.redactor.text(self.skills.load(args["name"]))
            result = text_page(
                text,
                args["offset"],
                args["limit"],
            )
            if result.status != "success":
                return result
            if args["name"] not in state.active_skills:
                state.active_skills.append(args["name"])
            result.data["untrusted"] = True
            return result
        if name == "ask_user":
            if self.question_callback is None:
                return Outcome(
                    status="blocked", reason="user_input_unavailable", content=args["question"]
                )
            answer = self.question_callback(args["question"])
            if inspect.isawaitable(answer):
                answer = await answer
            if answer is None:
                return Outcome(status="blocked", reason="user_input_cancelled")
            return Outcome(status="success", content=str(answer))
        if name == "worker_start":
            data = await self.workers.start(
                args["task"], args.get("mode", "explore"), args.get("write_scope", [])
            )
            return Outcome(
                status="success" if data["status"] == "running" else "blocked",
                content=json.dumps(data, ensure_ascii=False),
                data=data,
            )
        if name == "worker_status":
            data = await self.workers.status(args["worker_id"])
            return Outcome(
                status="success" if data["status"] != "missing" else "blocked",
                content=json.dumps(data, ensure_ascii=False),
                data=data,
                changed_paths=[
                    p for p in data.get("changed_paths", []) if p not in state.changed_paths
                ],
            )
        if name == "worker_message":
            data = await self.workers.message(args["worker_id"], args["message"])
            return Outcome(
                status="success" if data["status"] == "queued" else "blocked",
                reason=data.get("reason", ""),
                content=json.dumps(data, ensure_ascii=False),
                data=data,
            )
        if name == "worker_stop":
            data = await self.workers.stop(args["worker_id"])
            return Outcome(
                status="success" if data["status"] == "cancelled" else "blocked",
                content=json.dumps(data, ensure_ascii=False),
                data=data,
            )
        return Outcome(status="blocked", reason="workflow_not_implemented", content=name)

    def finalize(self, answer: str) -> RunState:
        state = self.state
        assert state is not None
        if state.terminal:
            return state
        if self._operation_task is not None and self._operation_task is not asyncio.current_task():
            raise RuntimeError("runtime_busy: await the current operation before Final")
        self._settle_workers()
        feedback = completion_feedback(state) if self.settings.completion != "off" else ""
        if feedback and state.final_revisions < self.settings.max_final_revisions:
            state.final_revisions += 1
            state.feedback = feedback
            self.add_message("user", feedback, important=True)
            self.evidence.emit(state.run_id, "completion_revision", feedback=feedback)
            return state
        result = self.verifier.final(state)
        self.evidence.emit(state.run_id, "verifier_result", result=result.model_dump())
        if not result.passed and self.settings.completion == "strict":
            state.feedback = result.reason
            self.finish("completion_requirements_failed")
            return state
        state.final = answer
        state.phase = "completed"
        state.stop_reason = "verified" if result.passed else "completed_with_warning"
        self.evidence.emit(state.run_id, "run_completed", result=result.model_dump(), answer=answer)
        self._stop_hook()
        self._auto_dream()
        self.report(result)
        return state

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
            },
        )

    def add_message(self, role: str, content: str, *, important=False, native=False):
        if self.state:
            self.state.messages.append(
                {"role": role, "content": content, "important": important, "native": native}
            )
            if role == "user":
                target = self._pending_messages if self._in_turn else self._structured_history
                target.append({"role": "user", "content": content})

    def _memory_text(self, query: str) -> str:
        lessons = self.memory.retrieve(query, limit=3)
        if self.state:
            self.state.recalled_ids = sorted(
                set(self.state.recalled_ids) | {x["id"] for x in lessons}
            )
        return "\n".join(
            f"- {x['text']} (evidence: {x['provenance'].get('evidence_id', '')})" for x in lessons
        )

    def _unavailable_reason(self, name):
        if name == "observation_read" and not (
            self.state
            and (
                any(item.get("type") == "function_call_output" for item in self._structured_history)
                or any(item.get("role") == "tool" for item in self.state.messages)
                or self.evidence.path("runs", self.state.run_id, "observations").is_dir()
            )
        ):
            return "no_archived_observations"
        if self.settings.sandbox == "off" and (
            name == "run_tests"
            or (
                name in {"run_shell", "write_file", "patch_file"}
                and (
                    self.settings.read_only
                    or self.settings.write_scope is not None
                    or (self.state and self.state.mode == "plan")
                )
            )
        ):
            return "isolation_required_for_scope"
        if name in {"write_file", "patch_file"}:
            if self.settings.read_only:
                return "read_only"
            if self.state and self.state.mode == "plan":
                return "plan_mode"
            if self.settings.write_scope == []:
                return "write_scope"
        if name == "plan_exit" and self.settings.read_only:
            return "read_only"
        if name.startswith("memory_") and not self.settings.memory_enabled:
            return "memory_disabled"
        if name.startswith("worker_") and self.settings.max_workers == 0:
            return "workers_disabled"
        if name == "ask_user" and self.question_callback is None:
            return "user_input_unavailable"
        no_approval = self.settings.approval == "never" or (
            self.settings.approval == "ask" and self.approval_callback is None
        )
        if no_approval and (
            name in {"write_file", "patch_file", "plan_exit"}
            or (name in {"run_shell", "run_tests"} and not self._has_allowed_command(name))
        ):
            return "approval_unavailable"
        return ""

    def _has_allowed_command(self, name):
        """An allow rule must apply to this tool and survive higher-priority denials."""
        for rule in self.settings.rules:
            if rule.decision != "allow":
                continue
            for prefix in TEST_COMMAND_PREFIXES if name == "run_tests" else [rule.prefix]:
                if rule.prefix[: len(prefix)] == prefix:
                    candidate = rule.prefix
                elif prefix[: len(rule.prefix)] == rule.prefix:
                    candidate = prefix
                else:
                    continue
                decision = permission(
                    name,
                    "process",
                    {"command": shlex.join(candidate)},
                    self.settings,
                    self.state.mode if self.state else "work",
                )
                if decision.action == "allow":
                    return True
        return False

    def _tool_names(self):
        selected = (
            SCHEMAS
            if self.settings.tool_profile == "all"
            else (set(CORE_TOOLS) | set(self.state.loaded_tools if self.state else []))
        )
        return sorted(name for name in selected if not self._unavailable_reason(name))

    def tool_definitions(self):
        available = [name for name in SCHEMAS if not self._unavailable_reason(name)]
        return definitions(self._tool_names(), available=available)

    def _auto_dream(self):
        if self._dream_ran or not self.settings.auto_dream:
            return
        self._dream_ran = True
        try:
            self.memory.dream()
            if self.state:
                self.evidence.emit(self.state.run_id, "memory_dream", status="completed")
        except Exception as exc:
            if self.state:
                self.evidence.emit(
                    self.state.run_id, "memory_dream", status="failed", error=type(exc).__name__
                )
