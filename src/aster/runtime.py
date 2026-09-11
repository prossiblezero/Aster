"""One transition engine used by autonomous runs, CLI and the RL adapter."""

from __future__ import annotations

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
from .tools import SCHEMAS, ToolContext, ToolRegistry, definitions
from .verifier import Verifier
from .workspace import Workspace


class Runtime:
    def __init__(self, root: Path, settings: Settings, *, model: ModelClient | None = None,
                 approval_callback: Callable[[ToolCall, Decision], bool] | None = None):
        self.workspace = Workspace(root)
        self.settings = settings
        self.approval_callback = approval_callback or (lambda _call, _decision: False)
        secrets = [settings.api_key.get_secret_value()]
        self.evidence = EvidenceStore(self.workspace.root, Redactor(secrets))
        self.memory = Memory(self.workspace.root, settings.memory_enabled, settings.memory_evolution)
        self.model = model or ModelClient(settings)
        self.sandbox = SandboxRunner(settings)
        self.skills = Skills(self.workspace.root)
        self.context = ContextBuilder(settings.context_tokens, settings.output_tokens)
        self.hooks = Hooks()
        self.verifier = Verifier(self.workspace)
        self.stall = StallDetector()
        self.state: RunState | None = None
        self._initial_snapshot: dict[str, str] = {}
        self._conversation: list[dict] = []
        self._recovery_used = 0

    def start(self, task: TaskSpec) -> RunState:
        inferred = self.verifier.infer(task.prompt)
        for key, value in inferred.items():
            if not getattr(task, key):
                setattr(task, key, value)
        self.state = RunState(task=task, phase="running")
        self._initial_snapshot = self.workspace.snapshot()
        # The first turn is rebuilt by ContextBuilder so system policy and untrusted-data
        # boundaries are actually sent to the model; this seed is only for the task state.
        self._conversation = []
        self.evidence.emit(self.state.run_id, "run_started", task=task.model_dump(),
                           workspace_hash=self.workspace.fingerprint(), config=self.settings.identity())
        self.checkpoint()
        return self.state

    def checkpoint(self):
        if not self.state:
            return
        self.evidence.save(("runs", self.state.run_id, "checkpoint.json"), {
            "state": self.state.model_dump(mode="json"), "workspace_hash": self.workspace.fingerprint(),
            "workspace": self.workspace.snapshot(), "runtime_identity": self.settings.identity(),
        })

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
        self._conversation = self.state.messages.copy()
        return self.state

    async def run(self, task: TaskSpec | None = None, *, max_steps: int | None = None) -> RunState:
        if task is not None:
            self.start(task)
        if not self.state:
            raise RuntimeError("start(task) first")
        limit = max_steps or self.state.task.max_steps
        while not self.state.terminal and self.state.step < limit:
            await self.turn()
        if not self.state.terminal:
            self.finish("step_limit")
        return self.state

    async def turn(self) -> RunState:
        state = self.state
        assert state is not None
        if state.terminal:
            return state
        state.turn += 1
        memory_text = self._memory_text(state.task.prompt)
        skills = "\n".join(self.skills.list())
        prompt, report = self.context.build(state, state.task.prompt, memory_text, skills)
        self.evidence.emit(state.run_id, "context_built", estimated_tokens=report.estimated_tokens,
                           budget=report.budget, compacted=report.compacted,
                           removed_messages=report.removed_messages, sections=report.sections)
        try:
            request_messages = prompt if state.turn == 1 else self._conversation
            reply = await self.model.complete(request_messages, definitions())
        except ProviderError as exc:
            state.feedback = str(exc)
            self.finish("provider_error", failed=True)
            return state
        state.usage.update(reply.usage)
        if state.turn == 1:
            self._conversation = list(prompt)
        self._conversation.extend(reply.raw or ([{"role": "assistant", "content": reply.text}] if reply.text else []))
        if reply.text:
            self.add_message("assistant", reply.text, important=True)
        self.evidence.emit(state.run_id, "model_reply", turn=state.turn, text=reply.text,
                           calls=[c.model_dump() for c in reply.calls], usage=reply.usage)
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
            self._conversation.append({"type": "function_call_output", "call_id": call.call_id,
                                       "output": outcome.content or json.dumps(outcome.model_dump())})
            self.add_message("tool", outcome.content or json.dumps(outcome.model_dump()),
                             important=outcome.status in {"error", "blocked"})
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
        state.messages.append({"role": "tool", "content": outcome.content[:6000],
                               "important": outcome.status != "success"})
        if outcome.status == "success" and call.name in {"read_file", "search", "list_files", "git_diff"}:
            state.observed = True
        if outcome.changed_paths:
            state.changed_paths = sorted(set(state.changed_paths + outcome.changed_paths))
        reason = self.stall.observe(call, outcome)
        self.evidence.emit(state.run_id, "tool_result", step=state.step, call=call.model_dump(),
                           outcome=outcome.model_dump(), stall_reason=reason)
        if reason:
            self.evidence.emit(state.run_id, "stall_detected", reason=reason)
            if self._recovery_used < self.settings.max_recovery:
                self._recovery_used += 1
                state.feedback = f"Recovery required ({reason}). Change approach and use fresh evidence."
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
        decision = permission(call.name, effect, call.arguments, self.settings, state.mode)
        self.evidence.emit(state.run_id, "permission_decision", call=call.model_dump(),
                           decision=decision.action, reason=decision.reason)
        if decision.action == "deny":
            return Outcome(status="blocked", reason=decision.reason)
        if decision.action == "ask" and not self.approval_callback(call, decision):
            return Outcome(status="blocked", reason="approval_denied")
        before = self.hooks.before_tool(state, call)
        if before.action == "deny":
            return Outcome(status="blocked", reason=before.feedback or "hook_denied")
        context = ToolContext(self.workspace, self.sandbox, state.run_id, state.todos, state.plan,
                              self.memory, self)
        registry = ToolRegistry(context)
        started = time.monotonic()
        outcome = await registry.execute(call)
        outcome.duration_ms = (time.monotonic() - started) * 1000
        outcome.evidence_id = self.evidence.emit(state.run_id, "tool_decision", call_id=call.call_id,
                                                 tool=call.name, status=outcome.status,
                                                 reason=outcome.reason)["hash"]
        if call.name == "run_tests":
            outcome.data["is_verification"] = True
            outcome.data["command"] = call.arguments.get("command", "")
        after = self.hooks.after_tool(state, call, outcome) if outcome.status == "success" \
            else self.hooks.after_failure(state, call, outcome)
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
            return Outcome(status="success", content=json.dumps(state.todos, ensure_ascii=False), data={"todos": state.todos})
        if name == "plan_enter":
            state.mode = "plan"
            return Outcome(status="success", content="plan mode enabled")
        if name == "plan_exit":
            state.mode = "work"
            return Outcome(status="success", content="plan mode disabled")
        if name == "git_diff":
            state.observed = True
            return Outcome(status="success", content=self.workspace.diff(self._initial_snapshot), data={"diff": True})
        if name == "memory_retrieve":
            lessons = self.memory.retrieve(args["query"])
            state.recalled_ids = [x["id"] for x in lessons]
            return Outcome(status="success", content=json.dumps(lessons, ensure_ascii=False), data={"lessons": lessons})
        if name == "memory_update":
            lesson = self.memory.record(args["text"], run_id=state.run_id, evidence_id=args["evidence_id"],
                                        evidence_type="user_confirmed", outcome="support")
            return Outcome(status="success" if lesson else "blocked", content=json.dumps(lesson or {}),
                           reason="" if lesson else "memory_provenance", data=lesson or {})
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
        self.report(result)
        return state

    def finish(self, reason: str, *, failed: bool = False):
        if not self.state:
            return
        self.state.phase = "failed" if failed else "stopped"
        self.state.stop_reason = reason
        self.evidence.emit(self.state.run_id, "run_stopped", reason=reason, failed=failed)
        self.report(VerifierResult(passed=False, reason=reason))

    def report(self, result: VerifierResult):
        if not self.state:
            return
        self.checkpoint()
        self.evidence.save(("runs", self.state.run_id, "report.json"), {
            "run_id": self.state.run_id, "phase": self.state.phase,
            "stop_reason": self.state.stop_reason, "verifier": result.model_dump(),
            "steps": self.state.step, "turns": self.state.turn,
            "changed_paths": self.state.changed_paths,
            "verification": self.state.verification.model_dump(),
            "usage": self.state.usage,
        })

    def add_message(self, role: str, content: str, *, important=False):
        if self.state:
            self.state.messages.append({"role": role, "content": content, "important": important})

    def _memory_text(self, query: str) -> str:
        lessons = self.memory.retrieve(query, limit=3)
        return "\n".join(f"- {x['text']} (evidence: {x['provenance'].get('evidence_id','')})" for x in lessons)
