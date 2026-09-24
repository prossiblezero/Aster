"""Serial workflow business operating on explicit feature services, never Runtime."""
from __future__ import annotations

import hashlib
import inspect
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from ..knowledge.memory import Memory, store_excerpt
from ..knowledge.skills import Skills
from ..schema import Outcome, RunState, SkillSnapshot
from ..sessions.checkpoint import redact_skill_snapshots
from ..sessions.lifecycle import read_session_observation
from ..storage.evidence import EvidenceStore
from ..storage.workspace import Workspace
from .schemas import SCHEMAS
from .workspace import text_page


@dataclass
class WorkflowTools:
    state: RunState
    workspace: Workspace
    memory: Memory
    skills: Skills
    evidence: EvidenceStore
    archive_sources: dict[str, str]
    initial_text: dict[str, str]
    initial_modes: dict[str, int]
    unavailable_reason: Callable[[str], str]
    settle_workers: Callable[[], None]
    close_workers: Callable[[], Awaitable]
    worker_start: Callable[..., Awaitable]
    worker_status: Callable[..., Awaitable]
    worker_message: Callable[..., Awaitable]
    worker_stop: Callable[..., Awaitable]
    question_callback: Callable | None

    async def execute(self, name: str, args: dict) -> Outcome:
        state = self.state
        assert state is not None
        if name == "observation_read":
            return read_session_observation(
                self.evidence, state.run_id, self.archive_sources, args
            )
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
                name: reason
                for name in requested
                if (reason := self.unavailable_reason(name))
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
            await self.close_workers()
            if state.terminal:
                return Outcome(status="blocked", reason="run_terminal")
            self.settle_workers()
            state.mode = "plan"
            return Outcome(status="success", content="plan mode enabled")
        if name == "plan_exit":
            state.mode = "work"
            return Outcome(status="success", content="plan mode disabled")
        if name == "git_diff":
            state.observed = True
            return Outcome(
                status="success",
                content=self.workspace.diff(self.initial_text, self.initial_modes),
                data={"diff": True},
            )
        if name == "memory_retrieve":
            lessons = self.memory.retrieve(args["query"])
            return Outcome(
                status="success",
                content=json.dumps(lessons, ensure_ascii=False),
                data={"lessons": lessons},
            )
        if name in {"memory_store", "memory_update"}:
            return store_excerpt(
                self.memory, self.evidence, state.run_id,
                text=args["text"], evidence_id=args["evidence_id"], tags=args.get("tags", []),
                lesson_id=args["lesson_id"] if name == "memory_update" else None,
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
            if self.evidence.redactor.text(args["name"]) != args["name"]:
                return Outcome(status="blocked", reason="skill_name_contains_credential")
            resource = args.get("resource", "")
            refresh = args.get("refresh", False)
            if resource and refresh:
                return Outcome(status="blocked", reason="skill_resource_refresh")
            redact_skill_snapshots(state, self.evidence.redactor)
            previous = state.skill_snapshots.get(args["name"])
            text = (
                previous.body
                if previous is not None and not resource and not refresh
                else self.evidence.redactor.text(self.skills.load(args["name"], resource))
            )
            result = text_page(
                text,
                args["offset"],
                args["limit"],
            )
            if result.status != "success":
                return result
            if not resource:
                snapshot = SkillSnapshot(
                    body=text, sha256=hashlib.sha256(text.encode()).hexdigest()
                )
                state.skill_snapshots[args["name"]] = snapshot
                if args["name"] not in state.active_skills:
                    state.active_skills.append(args["name"])
                result.data.update(
                    version=snapshot.sha256,
                    previous_version=previous.sha256 if previous else None,
                    updated=previous is not None and previous.sha256 != snapshot.sha256,
                )
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
            data = await self.worker_start(
                args["task"], args.get("mode", "explore"), args.get("write_scope", [])
            )
            return Outcome(
                status="success" if data["status"] == "running" else "blocked",
                content=json.dumps(data, ensure_ascii=False),
                data=data,
            )
        if name == "worker_status":
            data = await self.worker_status(args["worker_id"], args.get("wait_seconds", 0))
            return Outcome(
                status="success" if data["status"] != "missing" else "blocked",
                content=json.dumps(data, ensure_ascii=False),
                data=data,
                changed_paths=[
                    p for p in data.get("changed_paths", []) if p not in state.changed_paths
                ],
            )
        if name == "worker_message":
            data = await self.worker_message(args["worker_id"], args["message"])
            return Outcome(
                status="success" if data["status"] == "queued" else "blocked",
                reason=data.get("reason", ""),
                content=json.dumps(data, ensure_ascii=False),
                data=data,
            )
        if name == "worker_stop":
            data = await self.worker_stop(args["worker_id"])
            return Outcome(
                status="success" if data["status"] == "cancelled" else "blocked",
                content=json.dumps(data, ensure_ascii=False),
                data=data,
            )
        return Outcome(status="blocked", reason="workflow_not_implemented", content=name)
