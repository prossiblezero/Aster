"""Bounded worker manager. Workers use Runtime again, but with narrower permissions."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass

from .config import Settings
from .schema import TaskSpec

if False:
    from .runtime import Runtime


@dataclass
class Worker:
    worker_id: str
    task: str
    mode: str
    runtime: Runtime
    future: asyncio.Task


class WorkerManager:
    def __init__(self, runtime: Runtime):
        self.parent = runtime
        self.workers: dict[str, Worker] = {}

    async def start(self, task: str, mode: str = "explore", write_scope: list[str] | None = None):
        if len(self.workers) >= self.parent.settings.max_workers:
            return {"status": "blocked", "reason": "worker_limit"}
        worker_id = uuid.uuid4().hex[:12]
        values = self.parent.settings.model_dump()
        values.update(read_only=mode == "explore", approval="auto", write_scope=write_scope or [])
        settings = Settings.model_validate(values)
        from .runtime import Runtime

        worker_runtime = Runtime(
            self.parent.workspace.root,
            settings,
            model=self.parent.model,
            approval_callback=lambda _call, _decision: mode != "explore",
        )
        future = asyncio.create_task(worker_runtime.run(TaskSpec(prompt=task, max_steps=12)))
        self.workers[worker_id] = Worker(worker_id, task, mode, worker_runtime, future)
        self.parent.evidence.emit(
            self.parent.state.run_id,
            "worker_started",
            worker_id=worker_id,
            task=task,
            mode=mode,
            write_scope=write_scope or [],
        )
        return {"status": "running", "worker_id": worker_id, "mode": mode}

    async def status(self, worker_id: str):
        worker = self.workers.get(worker_id)
        if not worker:
            return {"status": "missing", "worker_id": worker_id}
        state = worker.runtime.state
        if worker.future.done():
            try:
                result = worker.future.result()
                return {
                    "status": result.phase,
                    "worker_id": worker_id,
                    "stop_reason": result.stop_reason,
                    "answer": result.final,
                    "steps": result.step,
                }
            except Exception as exc:
                return {"status": "failed", "worker_id": worker_id, "error": str(exc)}
        return {
            "status": state.phase if state else "starting",
            "worker_id": worker_id,
            "steps": state.step if state else 0,
        }

    async def message(self, worker_id: str, message: str):
        return {
            "status": "unsupported",
            "worker_id": worker_id,
            "reason": "workers are bounded runs; start a new worker with revised scope",
        }

    async def stop(self, worker_id: str):
        worker = self.workers.get(worker_id)
        if not worker:
            return {"status": "missing", "worker_id": worker_id}
        worker.future.cancel()
        if worker.runtime.state:
            worker.runtime.finish("worker_cancelled")
        return {"status": "cancelled", "worker_id": worker_id}
