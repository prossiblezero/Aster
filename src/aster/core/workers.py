"""Small, bounded workers with narrower permissions and turn-boundary steering."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
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
    future: asyncio.Task
    inbox: list[str]


class WorkerManager:
    def __init__(self, runtime: Runtime):
        self.parent = runtime
        self.workers: dict[str, Worker] = {}

    async def start(self, task: str, mode: str = "explore", write_scope: list[str] | None = None):
        if self.parent.state is None or self.parent.state.terminal:
            return {"status": "blocked", "reason": "run_terminal"}
        active = [worker for worker in self.workers.values() if not worker.future.done()]
        if len(active) >= self.parent.settings.max_workers:
            return {"status": "blocked", "reason": "worker_limit"}
        if not task.strip() or mode not in {"explore", "worker"}:
            return {"status": "blocked", "reason": "invalid_worker_request"}
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
        if not read_only and any(
            _within(a, b) or _within(b, a)
            for worker in active
            if not worker.runtime.settings.read_only
            for a in scope
            for b in worker.runtime.settings.write_scope or []
        ):
            return {"status": "blocked", "reason": "worker_scope_busy"}
        settings = Settings.model_validate(
            {
                **self.parent.settings.model_dump(),
                "read_only": read_only,
                "write_scope": [] if read_only else scope,
                "max_workers": 0,
                # Workers have their own working memory. The parent owns durable promotion.
                "memory_enabled": False,
                "memory_evolution": False,
                "auto_dream": False,
            }
        )
        from .runtime import Runtime

        model = (
            ModelClient(settings) if type(self.parent.model) is ModelClient else self.parent.model
        )
        runtime = Runtime(
            self.parent.workspace.root,
            settings,
            model=model,
            approval_callback=self.parent.approval_callback,
            question_callback=self.parent.question_callback,
            hooks=self.parent.hooks,
        )
        runtime._managed_worker = True
        # Infer from the assignment, not from the handoff guidance appended below.
        spec = TaskSpec(prompt=task, max_steps=12, max_turns=12, **runtime.verifier.infer(task))
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
        runtime.state.parent_run_id = self.parent.state.run_id
        runtime.checkpoint()
        worker_id = uuid.uuid4().hex[:12]
        inbox: list[str] = []
        future = asyncio.create_task(self._run(runtime, inbox), name=f"aster-worker-{worker_id}")
        self.workers[worker_id] = Worker(worker_id, task, mode, runtime, future, inbox)
        self.parent.evidence.emit(
            self.parent.state.run_id,
            "worker_started",
            worker_id=worker_id,
            child_run_id=runtime.state.run_id,
            task=task,
            mode=mode,
            write_scope=settings.write_scope,
            verification_owner="parent" if parent_verifies else "worker",
        )
        return {"status": "running", "worker_id": worker_id, "mode": mode}

    async def _run(self, runtime: Runtime, inbox: list[str]):
        try:
            while not runtime.state.terminal:
                for message in inbox:
                    runtime.add_message("user", message, important=True)
                    runtime.evidence.emit(
                        runtime.state.run_id, "worker_message_delivered", text=message
                    )
                inbox.clear()
                state = runtime.state
                reason = (
                    "step_limit" if state.step >= state.task.max_steps else
                    "turn_limit" if state.turn >= state.task.max_turns - 1 else ""
                )
                if reason:
                    runtime._handoff_reason = reason
                    runtime.add_message(
                        "user", "Your work budget is exhausted. No more tools are available. "
                        "Return a concise PARTIAL handoff using only observed facts: findings, "
                        "paths/symbols, changes, checks actually run, and remaining gaps. "
                        "Do not claim the assignment is complete.", important=True,
                    )
                await runtime.turn()
                if reason and not state.terminal:
                    runtime.finish(reason)
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
            "delivery": "complete" if state.phase == "completed" else "partial",
            "stop_reason": state.stop_reason,
            "answer": state.final,
            "read_evidence": state.read_evidence,
            "changed_paths": state.changed_paths,
            "verification": state.verification.model_dump(),
        }
        runtime.evidence.save(("runs", state.run_id, "handoff.json"), handoff)
        runtime.checkpoint()
        return handoff

    async def status(self, worker_id: str | list[str], wait_seconds: float = 0):
        if isinstance(worker_id, list):
            ids = list(dict.fromkeys(worker_id))
            pending = {
                self.workers[key].future
                for key in ids
                if key in self.workers and not self.workers[key].future.done()
            }
            if pending and wait_seconds > 0:
                await asyncio.wait(pending, timeout=wait_seconds)
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
            return {"status": "missing", "worker_id": worker_id}
        if wait_seconds > 0 and not worker.future.done():
            # Return child failures/cancellation as status; caller cancellation still
            # propagates, and neither it nor the timeout cancels the child.
            await asyncio.wait({worker.future}, timeout=wait_seconds)
        state = worker.runtime.state
        details = {
            "worker_id": worker_id,
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
                stop_reason=state.stop_reason,
            )
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

    async def message(self, worker_id: str, message: str):
        worker = self.workers.get(worker_id)
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
        return {"status": "queued", "worker_id": worker_id}

    async def stop(self, worker_id: str):
        worker = self.workers.get(worker_id)
        if not worker:
            return {"status": "missing", "worker_id": worker_id}
        if worker.future.done():
            return await self.status(worker_id)
        worker.runtime.abort("worker_cancelled")
        if not worker.future.cancelling():
            worker.future.cancel()
        await asyncio.gather(worker.future, return_exceptions=True)
        # A coroutine cancelled before its first turn has not entered its finally block.
        if not worker.runtime.state.terminal:
            worker.runtime.abort("worker_cancelled")
        self._save_handoff(worker.runtime)
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
