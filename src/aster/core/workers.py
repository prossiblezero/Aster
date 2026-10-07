"""Small, bounded workers with narrower permissions and turn-boundary steering."""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from ..config import Settings
from ..providers.client import ModelClient
from ..schema import TaskSpec

if TYPE_CHECKING:
    from .runtime import Runtime


def _within(path: str, directory: str) -> bool:
    return path == directory or path.startswith(directory.rstrip("/") + "/")


def _scope(requested: list[str], parent: list[str] | None) -> list[str]:
    """Intersect scopes, accepting only ordinary workspace-relative paths."""
    normalized = []
    for value in requested:
        path = PurePosixPath(value)
        if not value or path.is_absolute() or ".." in path.parts or str(path) == ".":
            raise ValueError("invalid_write_scope")
        normalized.append(str(path))
    if parent is None:
        return sorted(set(normalized))
    return sorted(
        {
            child if _within(child, allowed) else allowed
            for child in normalized
            for allowed in parent
            if _within(child, allowed) or _within(allowed, child)
        }
    )


@dataclass
class Worker:
    worker_id: str
    task: str
    mode: str
    runtime: Runtime
    future: asyncio.Future
    inbox: list[str]
    delivered: bool = False
    parent_policy: dict = field(default_factory=dict)
    todo_id: str = ""
    explicit: bool = False


class TeamBudget:
    """Count admissions synchronously; persist reservations before starting requests."""

    def __init__(self, parent: Runtime):
        self.parent = parent

    def remaining(self, kind: str) -> int:
        state = self.parent.state
        limit = state.task.max_turns if kind == "model_calls" else state.task.max_steps
        return limit - state.team_usage.get(kind, 0)

    def reserve(self, kind: str) -> int:
        task = self.parent.state.task
        limit = task.max_turns if kind == "model_calls" else task.max_steps
        return max(2, limit // 5)

    def token_reserve(self) -> int:
        # Reserve two parent-sized requests for integration/review and final delivery.
        return max((self.parent.state.task.max_tokens or 0) // 5,
                   2 * self.parent._request_estimate)

    def admit(self, runtime: Runtime, kind: str, tokens: int = 0) -> str:
        state = self.parent.state
        child = runtime is not self.parent
        reserve = self.reserve(kind) if child else 0
        if self.remaining(kind) <= reserve:
            return "team_" + kind + "_limit"
        if kind == "model_calls" and state.task.max_tokens is not None:
            token_reserve = self.token_reserve() if child else 0
            if state.team_usage.get("charged_tokens", 0) + tokens + token_reserve > state.task.max_tokens:
                return "team_token_limit"
        state.team_usage[kind] = state.team_usage.get(kind, 0) + 1
        if kind == "model_calls":
            state.team_usage["charged_tokens"] = state.team_usage.get("charged_tokens", 0) + tokens
        return ""

    def settle(self, reservation: int, usage: dict):
        state = self.parent.state
        if "total_tokens" in usage:
            state.team_usage["charged_tokens"] += usage["total_tokens"] - reservation
        else:
            # Missing billing stays conservatively charged, never treated as free.
            state.team_usage["estimated_requests"] = state.team_usage.get("estimated_requests", 0) + 1


class WorkerManager:
    def __init__(self, runtime: Runtime):
        self.parent = runtime
        runtime._request_estimate = 0
        self.workers: dict[str, Worker] = {}
        self.budget = TeamBudget(runtime)
        if runtime.state and not runtime.state.team_usage:
            runtime.state.team_usage = {
                "model_calls": runtime.state.turn, "tool_calls": runtime.state.step,
                "charged_tokens": runtime.state.usage.get("total_tokens", 0),
            }

    def limits(self):
        token_limit = self.parent.state.task.max_tokens
        if token_limit is not None and (
            self.parent.state.team_usage.get("charged_tokens", 0) + self.budget.token_reserve()
            + self.parent.settings.effective_output_tokens > token_limit
        ):
            return 0, 0
        slots = self.parent.settings.max_workers or 2
        return (
            (self.budget.remaining("tool_calls") - self.budget.reserve("tool_calls")) // slots,
            (self.budget.remaining("model_calls") - self.budget.reserve("model_calls")) // slots,
        )

    def _settings(self, read_only, scope):
        return Settings.model_validate({
            **self.parent.settings.model_dump(), "read_only": read_only,
            "write_scope": [] if read_only else scope, "max_workers": 0,
            "memory_enabled": False, "memory_evolution": False, "auto_dream": False,
        })

    def _runtime(self, settings):
        from .runtime import Runtime

        model = ModelClient(settings) if type(self.parent.model) is ModelClient else self.parent.model
        runtime = Runtime(
            self.parent.workspace.root, settings, model=model,
            approval_callback=self.parent.approval_callback,
            question_callback=self.parent.question_callback, hooks=self.parent.hooks,
        )
        runtime._managed_worker = True
        return runtime

    def _remember(self, worker):
        self.parent.state.worker_sessions[worker.worker_id] = {
            "task": worker.task, "mode": worker.mode, "todo_id": worker.todo_id,
            "run_id": worker.runtime.state.run_id,
            "entry_id": worker.runtime.session_tree.cursor,
            "parent_run_id": worker.runtime.state.parent_run_id,
            "policy": worker.parent_policy,
            "read_only": worker.runtime.settings.read_only,
            "write_scope": worker.runtime.settings.write_scope,
            "explicit": worker.explicit,
            "pending_messages": list(worker.inbox),
        }

    def _saved(self, worker_id):
        record = self.parent.state.worker_sessions[worker_id]
        if not record.get("entry_id"):
            raise ValueError("worker_snapshot_unavailable")
        data = self.parent.session_tree.restore(record["entry_id"])
        if (data["state"]["run_id"] != record["run_id"]
                or data["state"]["parent_run_id"] != record["parent_run_id"]):
            raise ValueError("worker_checkpoint_identity_mismatch")
        return record, data

    def _restore(self, worker_id):
        record, _ = self._saved(worker_id)
        if record["policy"] != self.policy():
            raise ValueError("worker_permissions_changed")
        runtime = self._runtime(self._settings(record["read_only"], record["write_scope"]))
        try:
            runtime.resume(record["run_id"], entry_id=record.get("entry_id"),
                           allow_model_change=True, adopt_workspace=True)
        except Exception:
            runtime.sandbox.close()
            raise
        runtime._worker_owner = self
        settled = asyncio.get_running_loop().create_future()
        settled.set_result(runtime.state)
        worker = Worker(worker_id, record["task"], record["mode"], runtime, settled,
                        list(record.get("pending_messages", [])),
                        parent_policy=record["policy"], todo_id=record["todo_id"],
                        explicit=record.get("explicit", False))
        return worker

    def overview(self):
        """Read saved children without restarting them or constructing runtimes."""
        if not self.parent.state:
            return []
        return [self._snapshot(key) for key in dict.fromkeys([
            *self.parent.state.worker_sessions, *self.workers,
        ])]

    def policy(self):
        return {**self.parent._safety_identity(self.parent.settings.identity()),
                "mode": self.parent.state.mode}

    def writer_busy(self, runtime: Runtime | None = None):
        return any(
            not w.future.done() and not w.runtime.settings.read_only and w.runtime is not runtime
            for w in self.workers.values()
        )

    def acknowledge(self, data: dict):
        for item in data.get("workers", [data]):
            self._record_delivery(item, observed=True)
            worker = self.workers.get(item.get("worker_id"))
            if worker and item.get("delivery") and item.get("run_id") == worker.runtime.state.run_id:
                worker.delivered = True
            if item.get("delivery") and self.parent.state.pending_handoffs.get(item.get("worker_id")) == item.get("run_id"):
                self.parent.state.pending_handoffs.pop(item["worker_id"], None)

    @staticmethod
    def model_view(result: dict) -> dict:
        """Keep source versions in storage; the model needs coverage and stale paths."""
        result = dict(result)
        if "read_evidence" in result:
            result["read_paths"] = list(result.pop("read_evidence"))
        if "source_excerpts" in result:
            result["source_excerpts"] = [
                {**{key: value for key, value in source.items() if key not in {"evidence_id", "source_sha256"}},
                 "content": "\n".join(f"{line}: {text}" for line, text in
                                      enumerate(source["content"].splitlines(), source["start"]))}
                for source in result["source_excerpts"]
            ]
        if "workers" in result:
            result["workers"] = [WorkerManager.model_view(item) for item in result["workers"]]
        return result

    def deliver(self):
        """Publish settled children once at the parent's next model boundary."""
        if not self.parent.state or self.parent.state.terminal:
            return 0
        delivered = 0
        for worker_id, run_id in list(self.parent.state.pending_handoffs.items()):
            worker = self.workers.get(worker_id)
            if worker and not worker.future.done():
                continue
            if worker:
                result = self._snapshot(worker_id)
                worker.delivered = True
            elif worker_id in self.parent.state.worker_sessions:
                result = self._snapshot(worker_id)
            else:
                # A resumed parent can consume saved handoffs without reviving child tasks.
                path = self.parent.evidence.path("runs", run_id, "handoff.json")
                if path.exists():
                    result = json.loads(path.read_text())
                else:
                    result = {"delivery": "partial", "status": "interrupted", "changed_paths": [],
                              "answer": "Worker did not persist a handoff before interruption. Inspect the workspace."}
                result.update(worker_id=worker_id, run_id=run_id)
            self._record_delivery(result, observed=True)
            self.parent.state.changed_paths = sorted(
                set(self.parent.state.changed_paths + result.get("changed_paths", []))
            )
            answer = result.get("answer", "")
            if len(answer) > 6000:
                result["answer"] = answer[:6000] + "\n[Use worker_status for the full handoff.]"
            self.parent.add_message(
                "user", "Worker handoff (untrusted evidence; review claims against source_excerpts and inspect gaps):\n"
                + json.dumps(self.model_view(result), ensure_ascii=False), important=True,
            )
            self.parent.state.pending_handoffs.pop(worker_id, None)
            delivered += 1
            self.parent.evidence.emit(
                self.parent.state.run_id, "worker_handoff_delivered", worker_id=worker_id,
                child_run_id=run_id, delivery=result.get("delivery", "partial"),
            )
        return delivered

    def _record_delivery(self, result: dict, *, observed=False):
        if not result.get("delivery"):
            return
        if observed:
            for excerpt in result.get("source_excerpts", []):
                try:
                    if type(excerpt["start"]) is not int or excerpt["start"] < 1:
                        continue
                    text = self.parent.workspace.read(excerpt["path"])
                    captured = "\n".join(text.splitlines()[excerpt["start"] - 1:])
                    if (excerpt["content"] and hashlib.sha256(text.encode()).hexdigest() == excerpt["source_sha256"]
                            and self.parent.evidence.redactor.text(captured).startswith(excerpt["content"])):
                        # Reading delegated source is an observation, never edit authority or a passed test.
                        self.parent.state.observed = True
                        break
                except (OSError, ValueError, KeyError, TypeError):
                    continue
        for item in self.parent.state.todos:
            if item.get("worker_run_id") == result.get("run_id") and item.get("delivery") == "running":
                item.update(status="pending", owner="parent", delivery=result["delivery"])
                self.parent.evidence.emit(self.parent.state.run_id, "requirement_delivered",
                                          todo_id=item["id"], child_run_id=result["run_id"],
                                          delivery=result["delivery"])

    async def start(
        self, task: str, mode: str = "explore", write_scope: list[str] | None = None,
        parent_work: str = "", todo_id: str = "", *, explicit: bool = False,
    ):
        if self.parent.state is None or (self.parent.state.terminal and not explicit):
            return {"status": "blocked", "reason": "run_terminal"}
        if explicit and mode != "explore":
            return {"status": "blocked", "reason": "explicit_workers_are_read_only"}
        todo = next((item for item in self.parent.state.todos if item.get("id") == todo_id), None) if todo_id else None
        if todo_id and (todo is None or todo.get("status") in {"done", "cancelled"}
                        or todo.get("delivery") == "running"):
            return {"status": "blocked", "reason": "todo_unavailable", "todo_id": todo_id}
        active = [worker for worker in self.workers.values() if not worker.future.done()]
        if len(active) >= (self.parent.settings.max_workers or (2 if explicit else 0)):
            return {"status": "blocked", "reason": "worker_limit"}
        if not task.strip() or mode not in {"explore", "worker"}:
            return {"status": "blocked", "reason": "invalid_worker_request"}
        max_steps, max_turns = self.limits()
        if max_steps < 1 or max_turns < 2:
            return {"status": "blocked", "reason": "team_budget_reserved_for_parent"}
        parent_verifies = (
            self.parent.settings.completion == "strict"
            and self.parent.state.task.require_verification
        )
        try:
            scope = _scope(write_scope or [], self.parent.settings.write_scope)
        except ValueError as exc:
            return {"status": "blocked", "reason": str(exc)}
        read_only = (
            self.parent.settings.read_only or self.parent.state.mode == "plan" or mode == "explore"
        )
        if not read_only and not scope:
            return {"status": "blocked", "reason": "worker_write_scope_required"}
        if not read_only and self.writer_busy():
            return {"status": "blocked", "reason": "worker_scope_busy"}
        settings = self._settings(read_only, scope)
        runtime = self._runtime(settings)
        # Infer from the assignment, not from the handoff guidance appended below.
        spec = TaskSpec(prompt=task, max_steps=max_steps, max_turns=max_turns, **runtime.verifier.infer(task))
        parent_work = parent_work.strip() or "Integrate the handoff and verify the final result."
        spec.prompt = (
            "Your assigned subtask:\n" + task
            + "\n\nParent's separate work (do not duplicate):\n" + parent_work
            + "\n\nOriginal request, supplied verbatim as background for reproduction and "
            "acceptance criteria. Complete only your assigned subtask; flag relevant gaps "
            "outside your scope in the handoff:\n" + self.parent.state.task.prompt
        )
        if todo is not None:
            spec.prompt += "\n\nAssigned requirement and acceptance:\n" + json.dumps({
                "todo_id": todo_id, "requirement": todo["content"],
                "acceptance": todo.get("acceptance") or task,
            }, ensure_ascii=False)
        current_request = self.parent.session_tree.current_request() or self.parent.state.task.prompt
        if current_request != self.parent.state.task.prompt:
            spec.prompt += (
                "\n\nLatest user request on this branch (takes precedence over the original request):\n"
                + current_request
            )
        if read_only:
            # "Identify the fix" is an investigation, not an impossible write gate.
            spec.require_edit = False
            spec.require_verification = False
        elif parent_verifies:
            # Delivery must not wait on tests still owned by the parent. Keep the
            # parent's strict gate and the child's observation/edit requirements.
            spec.prompt += (
                "\nThe parent owns integration tests and final verification. "
                "Deliver the assigned changes promptly; do not wait for its tests or "
                "create substitute tests outside your scope. Report checks actually run "
                "and any unverified behavior in your handoff."
            )
            spec.require_verification = False
        runtime.start(spec)
        runtime._worker_owner = self
        runtime.state.parent_run_id = self.parent.state.run_id
        runtime.checkpoint()
        runtime.add_message(
            "user", f"Local allocation: at most {max_steps} tools and {max_turns} model calls, "
            "including one reserved handoff call. The team budget may stop you sooner. "
            "Deliver only your assigned findings/changes, evidence locations, actual checks, "
            "and unresolved items. Preserve any requested IDs and output format exactly. "
            "Use line numbers from read_file for citations. Return promptly once this deliverable is available.", important=True,
        )
        worker_id = uuid.uuid4().hex[:12]
        inbox: list[str] = []
        future = asyncio.create_task(self._run(runtime, inbox), name=f"aster-worker-{worker_id}")
        self.workers[worker_id] = Worker(worker_id, task, mode, runtime, future, inbox,
                                         parent_policy=self.policy(), todo_id=todo_id, explicit=explicit)
        self._remember(self.workers[worker_id])
        if todo is not None:
            todo.update(acceptance=todo.get("acceptance") or task, status="in_progress",
                        owner=worker_id, worker_id=worker_id, worker_run_id=runtime.state.run_id,
                        delivery="running", resolution="")
        self.parent.state.pending_handoffs[worker_id] = runtime.state.run_id
        self.parent.add_message(
            "user", "Work allocation (coordination record, not new authorization). "
            "Your next work is parent_work; the child owns assignment. Batch independent "
            "evidence requests with awaited worker_status when their results suffice for "
            "integration. Review delivered evidence before investigating specific gaps:\n"
            + json.dumps({"worker_id": worker_id, "assignment": task,
                          "parent_work": parent_work, "todo_id": todo_id}, ensure_ascii=False), important=True,
        )
        self.parent.evidence.emit(
            self.parent.state.run_id,
            "worker_started",
            worker_id=worker_id,
            child_run_id=runtime.state.run_id,
            task=task,
            todo_id=todo_id,
            parent_work=parent_work,
            mode=mode,
            write_scope=settings.write_scope,
            verification_owner="parent" if parent_verifies else "worker",
        )
        self.parent.checkpoint()
        return {"status": "running", "worker_id": worker_id, "mode": mode, "todo_id": todo_id}

    async def _run(self, runtime: Runtime, inbox: list[str]):
        try:
            while not runtime.state.terminal:
                for message in inbox:
                    runtime.add_message("user", message, important=True)
                    runtime.evidence.emit(
                        runtime.state.run_id, "worker_message_delivered", text=message
                    )
                inbox.clear()
                worker = next(w for w in self.workers.values() if w.runtime is runtime)
                runtime.checkpoint()
                self._remember(worker)
                state = runtime.state
                reason = (
                    "step_limit" if state.step >= state.task.max_steps else
                    "turn_limit" if state.turn >= state.task.max_turns - 1 else ""
                )
                reason = runtime._handoff_reason or reason
                if self.budget.remaining("model_calls") <= self.budget.reserve("model_calls") + 1:
                    reason = reason or "team_model_calls_limit"
                if reason:
                    runtime._begin_handoff(reason)
                await runtime.turn()
                self._remember(worker)
                self.parent.checkpoint()
                if inbox and state.phase == "completed":
                    # Guidance arriving during the final request must get a turn too.
                    result = self._continue(worker, "\n".join(inbox), launch=False)
                    if result["status"] != "queued":
                        self.parent.evidence.emit(self.parent.state.run_id,
                            "worker_message_pending", worker_id=worker.worker_id, reason=result["reason"])
            return runtime.state
        except asyncio.CancelledError:
            if not runtime.state.terminal:
                runtime.abort("worker_cancelled")
            raise
        except Exception:
            if not runtime.state.terminal:
                runtime.finish("worker_error", failed=True)
            raise
        finally:
            self._save_handoff(runtime)
            await runtime.workers.close()
            runtime.sandbox.close()
            self.parent.checkpoint()

    @staticmethod
    def _source_excerpts(working_memory, read_evidence, redactor=None, selected=()):
        if selected:
            return redactor.value(selected, mask_fields=False) if redactor else selected
        # ponytail: at most four existing 500-character excerpts; ask the child for gaps.
        excerpts = []
        for path, item in reversed(list(working_memory.items())):
            if path not in read_evidence or not item.get("evidence_id") or not item.get("summary"):
                continue
            raw_content, start = item["summary"], int(item["start"])
            content = redactor.text(raw_content) if redactor else raw_content
            excerpts.append({
                "path": path, "start": start, "end": start + len(content.splitlines()) - 1,
                "source_sha256": read_evidence[path], "evidence_id": item["evidence_id"],
                "content": content, "truncated": int(item["characters"]) > len(raw_content),
            })
            if len(excerpts) == 4:
                break
        return excerpts

    @staticmethod
    def _save_handoff(runtime: Runtime):
        state = runtime.state
        if not state.final:
            state.final = runtime.evidence.redactor.text(
                f"Partial handoff ({state.stop_reason or state.phase}). "
                f"Read paths: {', '.join(list(state.read_evidence)[:20]) or 'none'}. "
                f"Changed paths: {', '.join(state.changed_paths) or 'none'}. "
                f"Verification: {state.verification.state}. "
                "No model conclusion was delivered; inspect the saved evidence before continuing."
            )
        handoff = {
            "run_id": state.run_id,
            "status": state.phase,
            "delivery": "complete" if state.phase == "completed" else "partial",
            "stop_reason": state.stop_reason,
            "answer": state.final,
            "read_evidence": state.read_evidence,
            "source_excerpts": WorkerManager._source_excerpts(
                state.working_memory, state.read_evidence, runtime.evidence.redactor, state.handoff_sources
            ),
            "changed_paths": state.changed_paths,
            "verification": state.verification.model_dump(),
        }
        owner = runtime._worker_owner
        todo = next((item for item in owner.parent.state.todos
                     if item.get("worker_run_id") == state.run_id), None) if owner else None
        if todo:
            handoff.update(todo_id=todo["id"], acceptance=todo["acceptance"])
        runtime.evidence.save(("runs", state.run_id, "handoff.json"), handoff)
        if owner:
            owner._record_delivery(handoff)
        runtime.checkpoint()
        if owner:
            owner._remember(next(worker for worker in owner.workers.values() if worker.runtime is runtime))
        return handoff

    async def status(self, worker_id: str | list[str], wait_seconds: float = 0, wait_for: str = "any"):
        if isinstance(worker_id, list):
            ids = list(dict.fromkeys(worker_id))
            pending = {
                self.workers[key].future
                for key in ids
                if key in self.workers and not self.workers[key].future.done()
            }
            ready = bool(pending) and any(
                key in self.parent.state.pending_handoffs
                and (key not in self.workers or self.workers[key].future.done())
                for key in ids
            )
            if pending and wait_seconds > 0 and (wait_for == "all" or not ready):
                await asyncio.wait(
                    pending, timeout=wait_seconds,
                    return_when=asyncio.ALL_COMPLETED if wait_for == "all" else asyncio.FIRST_COMPLETED,
                )
            results = [await self.status(key) for key in ids]
            return {
                # Finished means no selected child is running, not that all succeeded.
                "status": "running" if any(not task.done() for task in pending) else "finished",
                "workers": results,
                "changed_paths": sorted(
                    {p for result in results for p in result.get("changed_paths", [])}
                ),
            }
        worker = self.workers.get(worker_id)
        if not worker:
            return self._snapshot(worker_id)
        if wait_seconds > 0 and not worker.future.done():
            # Return child failures/cancellation as status; caller cancellation still
            # propagates, and neither it nor the timeout cancels the child.
            await asyncio.wait({worker.future}, timeout=wait_seconds)
        return self._snapshot(worker_id)

    def _snapshot(self, worker_id: str):
        worker = self.workers.get(worker_id)
        if not worker:
            if not self.parent.state or worker_id not in self.parent.state.worker_sessions:
                return {"status": "missing", "worker_id": worker_id}
            try:
                record, saved = self._saved(worker_id)
                state = saved["state"]
                status = state["phase"]
                return self._staleness({
                    "worker_id": worker_id, "task": record["task"], "mode": record["mode"], "saved": True,
                    "run_id": record["run_id"], "todo_id": record["todo_id"],
                    "status": "interrupted" if status in {"running", "ready"} else status,
                    "delivery": "complete" if status == "completed" else "partial",
                    "answer": state["final"], "stop_reason": state["stop_reason"],
                    "steps": state["step"], "read_evidence": state["read_evidence"],
                    "source_excerpts": self._source_excerpts(
                        state["working_memory"], state["read_evidence"], self.parent.evidence.redactor,
                        state.get("handoff_sources", []),
                    ),
                    "changed_paths": state["changed_paths"], "verification": state["verification"],
                })
            except (OSError, ValueError, KeyError):
                return {"worker_id": worker_id, "status": "unavailable",
                        "run_id": self.parent.state.worker_sessions[worker_id].get("run_id", ""),
                        "task": self.parent.state.worker_sessions[worker_id].get("task", ""),
                        "reason": "worker_snapshot_unavailable", "delivery": "partial", "changed_paths": []}
        state = worker.runtime.state
        details = {
            "worker_id": worker_id,
            "task": worker.task, "mode": worker.mode, "run_id": state.run_id,
            "todo_id": worker.todo_id,
            "steps": state.step,
            "changed_paths": state.changed_paths,
            "verification": state.verification.model_dump(),
        }
        if worker.future.done():
            details.update(
                run_id=state.run_id,
                delivery="complete" if state.phase == "completed" else "partial",
                answer=state.final,
                read_evidence=state.read_evidence,
                source_excerpts=self._source_excerpts(
                    state.working_memory, state.read_evidence, worker.runtime.evidence.redactor, state.handoff_sources
                ),
                stop_reason=state.stop_reason,
            )
            details = self._staleness(details)
        if worker.future.cancelled():
            return {**details, "status": "cancelled"}
        if worker.future.done():
            try:
                result = worker.future.result()
                return {
                    **details,
                    "status": result.phase,
                    "stop_reason": result.stop_reason,
                    "answer": result.final,
                }
            except Exception as exc:
                return {
                    **details,
                    "status": "failed",
                    "error": type(exc).__name__,
                }
        return {**details, "status": state.phase}

    def _staleness(self, details):
        stale = []
        sources = {**details.get("read_evidence", {}), **{
            item["path"]: item["source_sha256"] for item in details.get("source_excerpts", [])
        }}
        for path, expected in sources.items():
            try:
                content = self.parent.workspace.read(path)
                if hashlib.sha256(content.encode()).hexdigest() == expected:
                    continue
            except (OSError, ValueError):
                pass
            stale.append(path)
        return {**details, "stale_paths": stale}

    def _continue(self, worker, message, *, launch=True):
        if self.parent.state.terminal and not worker.runtime.settings.read_only:
            return {"status": "blocked", "reason": "worker_requires_active_parent"}
        todo = next((item for item in self.parent.state.todos if item.get("id") == worker.todo_id), None) if worker.todo_id else None
        if worker.todo_id and (not todo or todo.get("status") in {"done", "cancelled"}
                               or todo.get("worker_run_id") != worker.runtime.state.run_id):
            return {"status": "blocked", "reason": "todo_unavailable"}
        capacity = self.parent.settings.max_workers or (2 if worker.explicit else 0)
        if sum(not w.future.done() and w is not worker for w in self.workers.values()) >= capacity:
            return {"status": "blocked", "reason": "worker_limit"}
        if worker.parent_policy != self.policy():
            return {"status": "blocked", "reason": "worker_permissions_changed"}
        max_steps, max_turns = self.limits()
        if max_steps < 1 or max_turns < 2:
            return {"status": "blocked", "reason": "worker_continuation_unavailable"}
        if not worker.runtime.settings.read_only and self.writer_busy(worker.runtime):
            return {"status": "blocked", "reason": "worker_scope_busy"}
        worker.runtime.continue_task(
            "\n".join([*worker.inbox, message]) if launch else message,
            adopt_workspace=True, max_steps=max_steps, max_turns=max_turns,
        )
        worker.inbox.clear()
        worker.runtime.state.parent_run_id = self.parent.state.run_id
        spec = worker.runtime.state.task
        if worker.runtime.settings.read_only:
            spec.require_edit = spec.require_verification = False
        elif self.parent.settings.completion == "strict" and self.parent.state.task.require_verification:
            spec.require_verification = False
        if todo is not None:
            todo.update(status="in_progress", owner=worker.worker_id, delivery="running",
                        worker_run_id=worker.runtime.state.run_id, resolution="")
        worker.delivered = False
        worker.runtime.checkpoint()
        self._remember(worker)
        self.parent.state.pending_handoffs[worker.worker_id] = worker.runtime.state.run_id
        self.parent.evidence.emit(
            self.parent.state.run_id, "worker_started", worker_id=worker.worker_id,
            child_run_id=worker.runtime.state.run_id, continuation=True,
        )
        if launch:
            worker.future = asyncio.create_task(self._run(worker.runtime, worker.inbox))
        self.parent.checkpoint()
        return {"status": "queued", "worker_id": worker.worker_id, "continued": True}

    async def message(self, worker_id: str, message: str, resume: bool = False):
        if not message.strip() or len(message) > 8192:
            return {"status": "blocked", "reason": "invalid_worker_message"}
        worker = self.workers.get(worker_id)
        if not worker and resume and self.parent.state and worker_id in self.parent.state.worker_sessions:
            try:
                worker = self._restore(worker_id)
            except (OSError, ValueError, KeyError) as exc:
                return {"status": "blocked", "detail": str(exc),
                        "reason": "worker_snapshot_unavailable" if str(exc) == "worker_snapshot_unavailable" else "worker_restore_blocked"}
        if worker and resume and worker.future.done() and message.strip() and len(message) <= 8192:
            result = self._continue(worker, message)
            if result["status"] == "queued":
                self.workers[worker_id] = worker
            else:
                worker.runtime.sandbox.close()
            return result
        reason = (
            "worker_missing"
            if not worker
            else "worker_finished"
            if worker.future.done() or worker.runtime.state.terminal
            else "worker_inbox_full"
            if len(worker.inbox) >= 8
            else "invalid_worker_message"
            if not message.strip() or len(message) > 8192
            else ""
        )
        if reason:
            return {"status": "blocked", "worker_id": worker_id, "reason": reason}
        worker.inbox.append(message)
        self._remember(worker)
        self.parent.checkpoint()
        return {"status": "queued", "worker_id": worker_id}

    async def stop(self, worker_id: str):
        worker = self.workers.get(worker_id)
        if not worker:
            return self._snapshot(worker_id)
        if worker.future.done():
            if not worker.runtime.state.final:
                self._save_handoff(worker.runtime)
                self.parent.checkpoint()
            return await self.status(worker_id)
        worker.runtime.abort("worker_cancelled")
        if not worker.future.cancelling():
            worker.future.cancel()
        await asyncio.gather(worker.future, return_exceptions=True)
        # A coroutine cancelled before its first turn has not entered its finally block.
        if not worker.runtime.state.terminal:
            worker.runtime.abort("worker_cancelled")
        self._save_handoff(worker.runtime)
        self.parent.checkpoint()
        return {"status": "cancelled", "worker_id": worker_id}

    async def close(self):
        await asyncio.gather(*(self.stop(worker_id) for worker_id in self.workers))

    def cancel(self):
        """Synchronous stop request; close() still awaits subprocess cleanup."""
        for worker in self.workers.values():
            if not worker.future.done():
                if not worker.future.cancelling():
                    worker.future.cancel()
                worker.runtime.abort("worker_cancelled")
