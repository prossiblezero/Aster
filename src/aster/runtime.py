"""One transition engine used by autonomous runs, CLI and the RL adapter."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from pathlib import Path

from .config import Settings
from .context import ContextBuilder
from .evidence import EvidenceStore, Redactor
from .hooks import Hooks
from .memory import Memory
from .provider import ModelClient, ProviderError
from .recovery import StallDetector, completion_feedback
from .safety.policy import Decision, permission
from .safety.runner import SandboxRunner
from .schema import Outcome, RunState, TaskSpec, ToolCall, VerifierResult
from .skills import Skills
from .tools import CORE_TOOLS, SCHEMAS, ToolContext, ToolRegistry, definitions
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
        approval_callback: Callable[[ToolCall, Decision], bool] | None = None,
        hooks: Hooks | None = None,
    ):
        self.workspace = Workspace(root)
        self.settings = settings
        self.approval_callback = approval_callback or (lambda _call, _decision: False)
        secrets = [settings.api_key.get_secret_value()]
        self.evidence = EvidenceStore(self.workspace.root, Redactor(secrets))
        self.memory = Memory(
            self.workspace.root, settings.memory_enabled, settings.memory_evolution
        )
        self.model = model or ModelClient(settings)
        self.sandbox = SandboxRunner(settings)
        self.skills = Skills(self.workspace.root)
        self.context = ContextBuilder(
            settings.context_tokens, settings.output_tokens, settings.tool_output_tokens
        )
        self.hooks = hooks or Hooks()
        self.verifier = Verifier(self.workspace)
        self.stall = StallDetector()
        self.state: RunState | None = None
        self._initial_snapshot: dict[str, str] = {}
        self._structured_history: list[dict] = []
        self._recovery_used = 0
        self._finish_hook_ran = False
        self._dream_ran = False
        self.workers = WorkerManager(self)

    def start(self, task: TaskSpec) -> RunState:
        inferred = self.verifier.infer(task.prompt)
        for key, value in inferred.items():
            if not getattr(task, key):
                setattr(task, key, value)
        self.state = RunState(task=task, phase="running")
        self._initial_snapshot = self.workspace.snapshot()
        # The first turn is rebuilt by ContextBuilder so system policy and untrusted-data
        # boundaries are actually sent to the model; this seed is only for the task state.
        self._structured_history = []
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
        if not self.state:
            return
        self.evidence.save(
            ("runs", self.state.run_id, "checkpoint.json"),
            {
                "state": self.state.model_dump(mode="json"),
                "workspace_hash": self.workspace.fingerprint(),
                "workspace": self.workspace.snapshot(),
                "runtime_identity": self.settings.identity(),
                "structured_history": self._structured_history[-80:],
            },
        )

    def resume(self, run_id: str) -> RunState:
        data = json.loads(self.evidence.path("runs", run_id, "checkpoint.json").read_text())
        if data["runtime_identity"] != self.settings.identity():
            raise ValueError("runtime_identity_mismatch")
        if data["workspace_hash"] != self.workspace.fingerprint():
            raise ValueError("workspace_mismatch")
        self.state = RunState.model_validate(data["state"])
        if self.state.terminal:
            return self.state
        self._initial_snapshot = data["workspace"]
        self._structured_history = data.get("structured_history", [])
        return self.state

    async def run(self, task: TaskSpec | None = None, *, max_steps: int | None = None) -> RunState:
        if task is not None:
            self.start(task)
        if not self.state:
            raise RuntimeError("start(task) first")
        limit = max_steps or self.state.task.max_steps
        while (
            not self.state.terminal
            and self.state.step < limit
            and self.state.turn < self.state.task.max_turns
        ):
            await self.turn()
        if not self.state.terminal:
            self.finish("turn_limit" if self.state.turn >= self.state.task.max_turns else "step_limit")
        return self.state

    async def turn(self) -> RunState:
        state = self.state
        assert state is not None
        if state.terminal:
            return state
        if state.turn >= state.task.max_turns:
            self.finish("turn_limit")
            return state
        state.turn += 1
        memory_text = self._memory_text(state.task.prompt)
        working_memory = self.memory.working_section(state.working_memory)
        skills = "\n".join(self.skills.list())
        prompt, report = self.context.build(
            state,
            state.task.prompt,
            memory_text,
            skills,
            self._structured_history if self.settings.protocol == "responses" else [],
            working_memory,
        )
        self.evidence.emit(
            state.run_id,
            "context_built",
            estimated_tokens=report.estimated_tokens,
            budget=report.budget,
            compacted=report.compacted,
            removed_messages=report.removed_messages,
            sections=report.sections,
        )
        try:
            request_messages = prompt
            reply = await self.model.complete(request_messages, definitions(self._tool_names()))
        except ProviderError as exc:
            state.feedback = str(exc)
            self.finish("provider_error", failed=True)
            return state
        for name, value in reply.usage.items():
            state.usage[name] = state.usage.get(name, 0) + value
        self._structured_history.extend(reply.raw)
        if reply.text:
            self.add_message("assistant", reply.text, important=True)
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
        for call in reply.calls:
            outcome = await self.step(call)
            if state.terminal:
                break
            output = outcome.content or json.dumps(outcome.model_dump(exclude={"data"}))
            if outcome.data.get("image_data"):
                output = [
                    {"type": "input_text", "text": outcome.content},
                    {"type": "input_image", "image_url": outcome.data["image_data"]},
                ]
            self._structured_history.append(
                {"type": "function_call_output", "call_id": call.call_id, "output": output}
            )
        self.checkpoint()
        return state

    async def step(self, call: ToolCall) -> Outcome:
        state = self.state
        assert state is not None
        state.step += 1
        if state.step > state.task.max_steps:
            self.finish("step_limit")
            return Outcome(status="blocked", reason="step_limit")
        outcome = await self._execute(call)
        self.verifier.observe(state, outcome)
        state.messages.append(
            {
                "role": "tool",
                "content": outcome.content,
                "important": outcome.status != "success",
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
            state.read_evidence[path] = hashlib.sha256(
                self.workspace.read(path).encode()
            ).hexdigest()
        elif outcome.status == "success" and call.name in {"search", "run_tests"}:
            self.memory.remember_file(
                f"__{call.name}__", outcome.content, state.working_memory
            )
        if outcome.status == "success" and call.name == "memory_retrieve":
            state.recalled_ids = [x["id"] for x in outcome.data.get("lessons", [])]
        if (
            outcome.status == "success"
            and state.recalled_ids
            and call.name in {"run_tests", "git_diff"}
        ):
            self.memory.record_use(state.recalled_ids, success=True)
        elif outcome.status in {"error", "blocked"} and state.recalled_ids:
            self.memory.record_use(state.recalled_ids, success=False)
        if outcome.changed_paths:
            state.changed_paths = sorted(set(state.changed_paths + outcome.changed_paths))
            for path in outcome.changed_paths:
                # A write invalidates any earlier file fact; the next read must refresh it.
                state.working_memory.pop(path, None)
            state.working_memory.pop("__search__", None)
            state.working_memory.pop("__run_tests__", None)
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
        if reason:
            self.evidence.emit(state.run_id, "stall_detected", reason=reason)
            if self._recovery_used < self.settings.max_recovery:
                self._recovery_used += 1
                state.feedback = (
                    f"Recovery required ({reason}). Change approach and use fresh evidence."
                )
                self.add_message("user", state.feedback, important=True)
            else:
                self.finish("stall_recovery_exhausted")
        self.checkpoint()
        return outcome

    async def _execute(self, call: ToolCall) -> Outcome:
        state = self.state
        assert state is not None
        if call.name not in SCHEMAS:
            return Outcome(status="blocked", reason="unknown_tool")
        _model, _description, effect = SCHEMAS[call.name]
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
        if decision.action == "ask" and not self.approval_callback(call, decision):
            return Outcome(status="blocked", reason="approval_denied")
        before = self.hooks.before_tool(state, call)
        if before.action == "stop":
            self.finish("hook_stop")
            return Outcome(status="blocked", reason=before.feedback or "hook_stop")
        if before.action == "deny":
            return Outcome(status="blocked", reason=before.feedback or "hook_denied")
        context = ToolContext(
            self.workspace, self.sandbox, state.run_id, state.todos, state.plan, self.memory, self
        )
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
            self.finish("hook_stop")
        state.used_call_ids.append(call.call_id)
        return outcome

    async def workflow_tool(self, name: str, args: dict) -> Outcome:
        state = self.state
        assert state is not None
        if name == "todo_list":
            return Outcome(
                status="success",
                content=json.dumps(state.todos, ensure_ascii=False),
                data={"todos": state.todos},
            )
        if name == "tool_load":
            requested = [tool for tool in args.get("names", []) if tool in SCHEMAS]
            state.loaded_tools = sorted(set(state.loaded_tools + requested))
            return Outcome(
                status="success",
                content=json.dumps(state.loaded_tools, ensure_ascii=False),
                data={"loaded": state.loaded_tools},
            )
        if name == "plan_enter":
            state.mode = "plan"
            return Outcome(status="success", content="plan mode enabled")
        if name == "plan_exit":
            state.mode = "work"
            return Outcome(status="success", content="plan mode disabled")
        if name == "git_diff":
            state.observed = True
            return Outcome(
                status="success",
                content=self.workspace.diff(self._initial_snapshot),
                data={"diff": True},
            )
        if name == "memory_retrieve":
            lessons = self.memory.retrieve(args["query"])
            state.recalled_ids = [x["id"] for x in lessons]
            return Outcome(
                status="success",
                content=json.dumps(lessons, ensure_ascii=False),
                data={"lessons": lessons},
            )
        if name == "memory_store":
            if not self.evidence.has_hash(state.run_id, args["evidence_id"]):
                return Outcome(status="blocked", reason="memory_provenance")
            lesson = self.memory.record(
                args["text"],
                run_id=state.run_id,
                evidence_id=args["evidence_id"],
                tags=args.get("tags", []),
            )
            return Outcome(
                status="success" if lesson else "blocked",
                reason="" if lesson else "memory_provenance",
                content=json.dumps(lesson or {}, ensure_ascii=False),
                data=lesson or {},
            )
        if name == "memory_update":
            if not self.evidence.has_hash(state.run_id, args["evidence_id"]):
                return Outcome(
                    status="blocked",
                    reason="memory_provenance",
                    content="evidence_id must reference an event in the current run trace",
                )
            lesson = self.memory.record(
                args["text"],
                run_id=state.run_id,
                evidence_id=args["evidence_id"],
                evidence_type="user_confirmed",
                outcome="support",
            )
            return Outcome(
                status="success" if lesson else "blocked",
                content=json.dumps(lesson or {}),
                reason="" if lesson else "memory_provenance",
                data=lesson or {},
            )
        if name == "memory_discard":
            for item in self.memory.state["lessons"]:
                if item["id"] == args["lesson_id"]:
                    item["contradictions"] += 1
            self.memory.save()
            return Outcome(status="success", content="lesson marked contradicted")
        if name == "skill_load":
            text = self.skills.load(args["name"])
            state.active_skills.append(args["name"])
            return Outcome(status="success", content=text, data={"untrusted": True})
        if name == "ask_user":
            return Outcome(status="blocked", reason="user_input_required", content=args["question"])
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
            )
        if name == "worker_message":
            data = await self.workers.message(args["worker_id"], args["message"])
            return Outcome(
                status="blocked",
                reason=data["reason"],
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
        feedback = completion_feedback(state)
        if feedback and state.final_revisions < self.settings.max_final_revisions:
            state.final_revisions += 1
            state.feedback = feedback
            self.add_message("user", feedback, important=True)
            self.evidence.emit(state.run_id, "completion_revision", feedback=feedback)
            return state
        result = self.verifier.final(state)
        self.evidence.emit(state.run_id, "verifier_result", result=result.model_dump())
        if not result.passed and self.settings.completion == "strict":
            self.finish("completion_requirements_failed")
            state.feedback = result.reason
            return state
        state.final = answer
        state.phase = "completed"
        state.stop_reason = "verified" if result.passed else "completed_with_warning"
        self.evidence.emit(state.run_id, "run_completed", result=result.model_dump(), answer=answer)
        self._auto_dream()
        self.report(result)
        return state

    def finish(self, reason: str, *, failed: bool = False):
        if not self.state:
            return
        if not self._finish_hook_ran:
            self._finish_hook_ran = True
            hook = self.hooks.stop(self.state)
            if hook.feedback:
                self.state.feedback = hook.feedback
        self.state.phase = "failed" if failed else "stopped"
        self.state.stop_reason = reason
        self.evidence.emit(self.state.run_id, "run_stopped", reason=reason, failed=failed)
        self._auto_dream()
        self.report(VerifierResult(passed=False, reason=reason))

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

    def add_message(self, role: str, content: str, *, important=False):
        if self.state:
            self.state.messages.append({"role": role, "content": content, "important": important})

    def _memory_text(self, query: str) -> str:
        lessons = self.memory.retrieve(query, limit=3)
        return "\n".join(
            f"- {x['text']} (evidence: {x['provenance'].get('evidence_id', '')})" for x in lessons
        )

    def _tool_names(self):
        if self.settings.tool_profile == "all":
            return list(SCHEMAS)
        return sorted(set(CORE_TOOLS) | set(self.state.loaded_tools if self.state else []))

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
                self.evidence.emit(self.state.run_id, "memory_dream", status="failed", error=type(exc).__name__)
