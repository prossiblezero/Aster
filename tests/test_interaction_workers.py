"""Exercise human decisions and workers through the real Runtime boundary."""

import asyncio
import json
import os
import sys

import pytest

from aster.cli import Terminal
from aster.config import ExecRule, Settings
from aster.core.runtime import Runtime
from aster.core.workers import _scope
from aster.providers.errors import ProviderError
from aster.schema import ModelReply, TaskSpec, ToolCall
from aster.ui.app import AsterTui
from aster.ui.widgets import HumanPrompt


class CallThenFinish:
    def __init__(self, name, arguments):
        self.call = ToolCall(name=name, arguments=arguments)
        self.requests = []

    async def complete(self, messages, tools):
        self.requests.append(messages)
        if len(self.requests) == 1:
            call = self.call
            return ModelReply(
                calls=[call],
                raw=[
                    {
                        "type": "function_call",
                        "name": call.name,
                        "call_id": call.call_id,
                        "arguments": json.dumps(call.arguments),
                    }
                ],
            )
        return ModelReply(text="Finished.")


class PausedModel(CallThenFinish):
    def __init__(self):
        super().__init__("list_files", {})
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def complete(self, messages, tools):
        if not self.requests:
            self.entered.set()
            await self.release.wait()
        return await super().complete(messages, tools)


async def wait_until(predicate):
    async def poll():
        while not predicate():
            await asyncio.sleep(0.01)

    await asyncio.wait_for(poll(), 5)


@pytest.mark.asyncio
async def test_tui_denies_approval_without_blocking_event_loop(tmp_path):
    app = AsterTui(tmp_path, Settings(approval="ask", completion="off"))
    app.runtime.model = CallThenFinish("write_file", {"path": "new.txt", "content": "hello"})
    async with app.run_test() as pilot:
        app.query_one("#prompt").value = "demo"
        await pilot.press("enter")
        await wait_until(lambda: isinstance(app.screen, HumanPrompt))
        assert not (tmp_path / "new.txt").exists()
        assert app.screen.focused.id == "decline"
        await pilot.click("#decline")
        await wait_until(lambda: app.task_worker.is_finished)
        assert not (tmp_path / "new.txt").exists()
        events = app.runtime.evidence.events(app.runtime.state.run_id)
        assert any(
            row["event"] == "tool_result" and row["outcome"]["reason"] == "approval_denied"
            for row in events
        )
        assert not app.query_one("#prompt").disabled


@pytest.mark.asyncio
async def test_tui_question_answer_reaches_next_model_turn(tmp_path):
    app = AsterTui(tmp_path, Settings(approval="ask", completion="off"))
    model = CallThenFinish("ask_user", {"question": "Which color?"})
    app.runtime.model = model
    async with app.run_test() as pilot:
        app.query_one("#prompt").value = "demo"
        await pilot.press("enter")
        await wait_until(lambda: isinstance(app.screen, HumanPrompt))
        app.screen.query_one("#answer").value = "blue"
        await pilot.press("enter")
        await wait_until(lambda: app.task_worker.is_finished)
        assert len(model.requests) == 2
        assert "blue" in json.dumps(model.requests[-1])


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["ctrl+c", "ctrl+q"])
async def test_tui_cancel_closes_pending_human_prompt(tmp_path, key):
    app = AsterTui(tmp_path, Settings(approval="ask", completion="off"))
    app.runtime.model = CallThenFinish("write_file", {"path": "new.txt", "content": "hello"})
    async with app.run_test() as pilot:
        app.query_one("#prompt").value = "demo"
        await pilot.press("enter")
        await wait_until(lambda: isinstance(app.screen, HumanPrompt))
        await pilot.press(key)
        await pilot.pause()
        assert app.runtime.state.terminal
        assert not isinstance(app.screen, HumanPrompt)
        assert not (tmp_path / "new.txt").exists()
        assert app.is_running == (key == "ctrl+c")


@pytest.mark.asyncio
@pytest.mark.parametrize(("read_only", "mode"), [(True, "work"), (False, "plan")])
async def test_worker_permissions_never_exceed_parent(tmp_path, read_only, mode):
    parent = Runtime(
        tmp_path,
        Settings(
            read_only=read_only,
            approval="never",
            write_scope=["src"],
            network=False,
            max_workers=2,
            memory_enabled=True,
        ),
        model=PausedModel(),
    )
    parent.start(TaskSpec(prompt="demo", require_verification=True))
    parent.state.mode = mode
    response = await parent.workers.start("demo", mode="worker", write_scope=["src", "outside"])
    child = parent.workers.workers[response["worker_id"]].runtime
    assert child.settings.read_only
    assert child.settings.approval == "never"
    assert child.settings.network is False
    assert child.settings.write_scope == []
    assert child.settings.max_workers == 0
    assert child.settings.memory_enabled is False
    assert child.state.parent_run_id == parent.state.run_id
    assert not child.state.task.require_edit and not child.state.task.require_observation
    nested = await child.workers.start("nested")
    assert nested["reason"] == "worker_limit"
    stopped = await parent.workers.stop(response["worker_id"])
    assert stopped["status"] == "cancelled"
    assert child.state.terminal
    assert parent.workers.workers[response["worker_id"]].future.done()


def test_worker_scope_intersection_does_not_widen_permissions():
    assert _scope(["src", "docs"], ["src/allowed", "tests"]) == ["src/allowed"]
    assert _scope(["src/sub/file.py"], ["src"]) == ["src/sub/file.py"]
    assert _scope(["src"], []) == []
    for invalid in ("../outside", "/tmp", ".", "src/../outside"):
        with pytest.raises(ValueError, match="invalid_write_scope"):
            _scope([invalid], None)


@pytest.mark.asyncio
async def test_worker_steering_and_concurrency_slot_reuse(tmp_path):
    model = PausedModel()
    parent = Runtime(tmp_path, Settings(max_workers=1, completion="off"), model=model)
    parent.start(TaskSpec(prompt="demo"))
    prompt, tools, report = parent.prepare_request()
    assert "Worker capability:" in prompt[0]["content"]
    assert "worker_start" not in {t["name"] for t in tools}
    preview = parent._preview_context(parent.state, [], parent.settings, {})
    assert preview.estimated_tokens == report.estimated_tokens
    started = await parent.workers.start("demo")
    worker_id = started["worker_id"]
    await model.entered.wait()
    rejected = await parent.workers.start("demo")
    assert rejected["reason"] == "worker_limit"
    queued = await parent.workers.message(worker_id, "Also check the license.")
    assert queued["status"] == "queued"
    assert not model.requests
    model.release.set()
    worker = parent.workers.workers[worker_id]
    child_prompt, _, _ = worker.runtime.prepare_request()
    assert "Worker capability:" not in child_prompt[0]["content"]
    await worker.future
    assert "Also check the license." in json.dumps(model.requests[-1])
    assert (await parent.workers.status(worker_id))["status"] == "completed"
    assert (await parent.workers.message(worker_id, "late"))["reason"] == "worker_finished"
    occupied = await parent.workers.start("Occupy the available worker slot")
    assert (await parent.workers.message(worker_id, "Follow up", resume=True))["reason"] == "worker_limit"
    await parent.workers.stop(occupied["worker_id"])
    parent.state.mode = "plan"
    assert (await parent.workers.message(worker_id, "Follow up", resume=True))[
        "reason"
    ] == "worker_permissions_changed"
    parent.state.mode = "work"
    previous_run = worker.runtime.state.run_id
    previous_usage = parent.state.team_usage.copy()
    continued = await parent.step(ToolCall(name="worker_message", arguments={
        "worker_id": worker_id, "message": "Explain the license finding.", "resume": True,
    }))
    assert continued.data["continued"] and continued.data["status"] == "queued"
    await worker.future
    assert worker.runtime.state.run_id != previous_run
    assert worker.runtime.state.task.max_steps <= 8 and worker.runtime.state.task.max_turns <= 4
    assert worker.runtime.settings.read_only and worker.runtime.settings.max_workers == 0
    assert "Also check the license." in json.dumps(model.requests[-1])
    assert "Explain the license finding." in json.dumps(model.requests[-1])
    assert parent.state.team_usage["model_calls"] == previous_usage["model_calls"] + 1
    assert (await parent.workers.message(worker_id, "Again", resume=True))[
        "reason"
    ] == "worker_continuation_unavailable"
    assert (await parent.workers.start("another task"))["status"] == "running"
    await parent.workers.close()


@pytest.mark.asyncio
async def test_worker_status_waits_without_cancelling_child(tmp_path):
    model = PausedModel()
    parent = Runtime(tmp_path, Settings(max_workers=1, completion="off"), model=model)
    parent.start(TaskSpec(prompt="demo"))
    started = await parent.workers.start("demo")
    worker_id = started["worker_id"]
    await model.entered.wait()

    timed_out = await parent.workers.status(worker_id, wait_seconds=0.01)
    assert timed_out["status"] == "running"
    assert not parent.workers.workers[worker_id].future.cancelled()

    waiter = asyncio.create_task(parent.workers.status(worker_id, wait_seconds=60))
    await asyncio.sleep(0)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert not parent.workers.workers[worker_id].future.done()

    model.release.set()
    finished = await parent.workers.status(worker_id, wait_seconds=1)
    assert finished["status"] == "completed"
    await parent.workers.close()


@pytest.mark.asyncio
async def test_worker_status_joins_results_with_one_deadline_and_preserves_cancellation(tmp_path):
    model = PausedModel()
    parent = Runtime(tmp_path, Settings(max_workers=2, completion="off"), model=model)
    parent.start(TaskSpec(prompt="demo"))
    first = (await parent.workers.start("demo"))["worker_id"]
    second = (await parent.workers.start("demo"))["worker_id"]
    await model.entered.wait()
    ids = [first, "missing", second, first]
    partial = await parent.workers.status(ids, wait_seconds=0.01)
    assert partial["status"] == "running"
    assert [r["worker_id"] for r in partial["workers"]] == ids[:3]
    assert partial["workers"][1]["status"] == "missing"
    waiting = asyncio.create_task(parent.workers.status(ids, wait_seconds=300))
    await asyncio.sleep(0)
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    assert all(not w.future.done() for w in parent.workers.workers.values())
    await parent.workers.stop(second)
    model.release.set()
    outcome = await parent.step(
        ToolCall(
            name="worker_status",
            arguments={"worker_id": ids, "wait_seconds": 300},
        )
    )
    assert outcome.status == "success" and outcome.data["status"] == "finished"
    assert [r["status"] for r in outcome.data["workers"]] == ["completed", "missing", "cancelled"]
    prompt, _, _ = parent.prepare_request()
    assert "Worker handoff (untrusted evidence" not in json.dumps(prompt)
    assert all(worker.delivered for worker in parent.workers.workers.values())
    assert not parent.state.terminal
    await parent.workers.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_child", [False, True])
async def test_waiting_tool_returns_child_failure_without_aborting_parent(tmp_path, cancel_child):
    entered, release = asyncio.Event(), asyncio.Event()

    class FailingModel:
        async def complete(self, *_):
            entered.set()
            await release.wait()
            raise RuntimeError("child failure")

    parent = Runtime(tmp_path, Settings(max_workers=1), model=FailingModel())
    parent.start(TaskSpec(prompt="demo"))
    started = await parent.workers.start("demo")
    worker_id = started["worker_id"]
    await entered.wait()
    waiting = asyncio.create_task(
        parent.step(
            ToolCall(
                name="worker_status",
                arguments={"worker_id": worker_id, "wait_seconds": 60},
            )
        )
    )
    await asyncio.sleep(0)
    assert not waiting.done()
    if cancel_child:
        await parent.workers.stop(worker_id)
    else:
        release.set()
    outcome = await asyncio.wait_for(waiting, 2)
    assert outcome.status == "success"
    assert outcome.data["status"] == ("cancelled" if cancel_child else "failed")
    assert outcome.data["verification"]["state"] == "missing"
    assert not parent.state.terminal
    await parent.workers.close()


@pytest.mark.parametrize("boundary", ["sequential", "parallel", "invalid_batch", "turn"])
async def test_worker_budget_delivers_one_tool_free_partial_handoff(tmp_path, boundary):
    (tmp_path / "target.py").write_text("answer = 42\n")
    calls = [ToolCall(name="read_file", arguments={"path": "target.py"}) for _ in range(3)]
    if boundary == "invalid_batch":
        calls[0] = ToolCall(name="read_file", arguments={})
    if boundary == "turn":
        calls = calls[:1]
    requests = []

    class Model:
        async def complete(self, messages, tools):
            requests.append((messages, tools))
            return ModelReply(calls=calls) if len(requests) == 1 else ModelReply(
                text="Partial: the assignment needs further investigation."
            )

    parent = Runtime(tmp_path, Settings(
        max_workers=1, completion="off", read_parallelism=1 if boundary == "sequential" else 2,
    ), model=Model())
    parent.start(TaskSpec(prompt="Inspect the repository"))
    started = await parent.workers.start("Inspect target.py")
    worker = parent.workers.workers[started["worker_id"]]
    worker.runtime.state.task.max_steps = 12 if boundary == "turn" else 2
    worker.runtime.state.task.max_turns = 2 if boundary == "turn" else 12
    result = await asyncio.wait_for(worker.future, 5)
    delivered = await parent.workers.status(worker.worker_id)
    assert len(requests) == 2 and requests[0][1] and requests[1][1] == []
    assert "PARTIAL handoff" in json.dumps(requests[1][0])
    assert result.step == (1 if boundary == "turn" else 2)
    assert result.phase == "stopped" and delivered["delivery"] == "partial"
    assert result.stop_reason == ("turn_limit" if boundary == "turn" else "step_limit")
    assert delivered["answer"] == result.final == "Partial: the assignment needs further investigation."
    outputs = [json.loads(item["output"]) for item in worker.runtime._structured_history
               if item.get("type") == "function_call_output"]
    assert len(outputs) == len(calls)
    assert sum(item["status"] == "success" for item in outputs) == (
        0 if boundary == "invalid_batch" else min(len(calls), 2)
    )
    assert bool(result.read_evidence) == (boundary != "invalid_batch")
    saved = json.loads(worker.runtime.evidence.path("runs", result.run_id, "handoff.json").read_text())
    assert saved["answer"] == delivered["answer"] and saved["delivery"] == "partial"
    await parent.workers.close()


@pytest.mark.parametrize("failure", ["provider", "cancelled", "error"])
async def test_worker_failed_handoff_preserves_observed_facts(tmp_path, failure):
    (tmp_path / "target.py").write_text("answer = 42\n")
    requests = []

    class Model:
        async def complete(self, messages, tools):
            requests.append(tools)
            if len(requests) == 1:
                return ModelReply(calls=[ToolCall(name="read_file", arguments={"path": "target.py"})])
            if failure == "provider":
                raise ProviderError("summary unavailable", category="transport")
            if failure == "cancelled":
                raise asyncio.CancelledError
            raise RuntimeError("summary failed")

    parent = Runtime(tmp_path, Settings(max_workers=1, completion="off"), model=Model())
    parent.start(TaskSpec(prompt="Inspect the repository"))
    started = await parent.workers.start("Inspect target.py")
    worker = parent.workers.workers[started["worker_id"]]
    worker.runtime.state.task.max_steps = 1
    await asyncio.wait_for(asyncio.gather(worker.future, return_exceptions=True), 5)
    delivered = await parent.workers.status(worker.worker_id)
    assert len(requests) == 2 and requests[-1] == []
    assert delivered["status"] == ("cancelled" if failure == "cancelled" else "failed")
    assert delivered["delivery"] == "partial" and "target.py" in delivered["answer"]
    assert delivered["read_evidence"] and delivered["verification"]["state"] == "missing"
    state = worker.runtime.state
    saved = json.loads(worker.runtime.evidence.path("runs", state.run_id, "handoff.json").read_text())
    assert saved["answer"] == state.final == delivered["answer"]
    assert saved["stop_reason"] == state.stop_reason and saved["delivery"] == "partial"
    assert not parent.state.terminal and (tmp_path / "target.py").read_text() == "answer = 42\n"
    await parent.workers.close()


async def test_worker_handoff_cannot_execute_unsolicited_tools(tmp_path):
    (tmp_path / "target.py").write_text("answer = 42\n")
    requests = []

    class Model:
        async def complete(self, messages, tools):
            requests.append(tools)
            if len(requests) == 1:
                return ModelReply(calls=[ToolCall(name="read_file", arguments={"path": "target.py"})])
            return ModelReply(calls=[ToolCall(
                name="write_file", arguments={"path": "target.py", "content": "unexpected"},
            )])

    parent = Runtime(tmp_path, Settings(approval="auto", max_workers=1, completion="off"), model=Model())
    parent.start(TaskSpec(prompt="Inspect the repository"))
    started = await parent.workers.start("Inspect target.py", "worker", ["target.py"])
    worker = parent.workers.workers[started["worker_id"]]
    worker.runtime.state.task.max_turns = 2
    result = await asyncio.wait_for(worker.future, 5)
    outputs = [json.loads(item["output"]) for item in worker.runtime._structured_history
               if item.get("type") == "function_call_output"]
    assert len(requests) == 2 and requests[-1] == [] and result.step == 1
    assert outputs[-1]["reason"] == "handoff_only"
    assert result.phase == "stopped" and result.final and not result.changed_paths
    assert (tmp_path / "target.py").read_text() == "answer = 42\n"
    assert (await parent.workers.status(worker.worker_id))["delivery"] == "partial"
    await parent.workers.close()


@pytest.mark.parametrize("limit", ["tool_calls", "model_calls", "tokens"])
async def test_team_budget_covers_children_parent_and_checkpoint_restore(tmp_path, limit):
    (tmp_path / "target.py").write_text("answer = 42\n")
    requests = []
    entered, release = asyncio.Event(), asyncio.Event()
    billed_tokens = 7

    class Model:
        async def complete(self, messages, tools):
            requests.append(tools)
            if limit == "tokens":
                entered.set()
                await release.wait()
                return ModelReply(text="Findings delivered.", usage={"total_tokens": billed_tokens})
            return ModelReply(
                calls=[ToolCall(name="read_file", arguments={"path": "target.py"})
                       for _ in range(6 if limit == "tool_calls" else 1)] if tools else [],
                text="Partial findings." if not tools else "", usage={"total_tokens": 7},
            )

    settings = Settings(max_workers=2, completion="off", read_parallelism=2)
    parent = Runtime(tmp_path, settings, model=Model())
    parent.start(TaskSpec(
        prompt="Inspect the repository", max_steps=10 if limit == "tool_calls" else 40,
        max_turns=8 if limit == "model_calls" else 20,
    ))
    for _ in range(2):
        assert (await parent.workers.start("Inspect target.py"))["status"] == "running"
    if limit == "tokens":
        first, second = parent.workers.workers.values()
        _, _, estimate = first.runtime.prepare_request()
        billed_tokens = estimate.estimated_tokens + first.runtime.settings.effective_output_tokens
        parent.state.task.max_tokens = billed_tokens * 3 // 2
        await asyncio.wait_for(entered.wait(), 5)
        await wait_until(second.future.done)
        assert len(requests) == 1 and not first.future.done()
        assert parent.state.team_usage["charged_tokens"] == billed_tokens
        assert second.runtime.state.stop_reason == "team_token_limit"
        release.set()
    await asyncio.wait_for(asyncio.gather(
        *(worker.future for worker in parent.workers.workers.values())
    ), 5)
    if limit == "tool_calls":
        assert parent.state.team_usage[limit] == 8
        for _ in range(2):
            assert (await parent.step(ToolCall(name="read_file", arguments={"path": "target.py"}))).status == "success"
        assert (await parent.step(ToolCall(name="list_files"))).reason == "team_tool_calls_limit"
        assert parent.state.team_usage[limit] == 10
    elif limit == "model_calls":
        assert parent.state.team_usage[limit] == 6
        for _ in range(3):
            await parent.turn()
        assert parent.state.team_usage[limit] == 8 and len(requests) == 8
    else:
        await parent.turn()
        assert len(requests) == 1
    assert parent.state.stop_reason == f"team_{'token' if limit == 'tokens' else limit}_limit"
    assert parent.state.team_usage.get("charged_tokens", 0) == len(requests) * billed_tokens
    assert parent.state.team_usage.get("model_calls", 0) == len(requests)
    assert all(worker.runtime.state.final for worker in parent.workers.workers.values())
    restored = Runtime(tmp_path, settings, model=parent.model)
    restored.resume(parent.state.run_id)
    assert restored.state.team_usage == parent.state.team_usage
    assert restored.state.task.max_tokens == parent.state.task.max_tokens
    await restored.turn()
    assert restored.state.team_usage == parent.state.team_usage
    await parent.workers.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("parent_verifies", [False, True])
async def test_worker_delivery_does_not_replace_parent_verification(tmp_path, parent_verifies):
    (tmp_path / "calc.py").write_text("def answer(): return 0\n")
    (tmp_path / "test_calc.py").write_text(
        "from calc import answer\ndef test_answer(): assert answer() == 42\n"
    )

    class ChildModel:
        def __init__(self):
            self.calls = iter(
                [
                    ToolCall(name="read_file", arguments={"path": "calc.py"}),
                    ToolCall(
                        name="write_file",
                        arguments={
                            "path": "calc.py",
                            "content": "def answer(): return 42\n",
                        },
                    ),
                ]
            )

        async def complete(self, *_):
            call = next(self.calls, None)
            return (
                ModelReply(calls=[call]) if call else ModelReply(text="Delivered; tests not run.")
            )

    parent = Runtime(
        tmp_path,
        Settings(approval="auto", max_workers=1, max_final_revisions=1),
        model=ChildModel(),
    )
    parent.start(
        TaskSpec(
            prompt="Implement and test answer() = 42.",
            require_verification=parent_verifies,
        )
    )
    prompt = parent.state.task.prompt
    started = await parent.step(
        ToolCall(
            name="worker_start",
            arguments={
                "task": "Implement answer() = 42 in calc.py; parent integration tests cover it.",
                "mode": "worker",
                "write_scope": ["calc.py"],
            },
        )
    )
    child = parent.workers.workers[started.data["worker_id"]].runtime
    assert child.state.task.require_verification == (not parent_verifies)
    assert child.state.task.require_edit and child.state.task.require_observation
    delivered = await parent.step(
        ToolCall(
            name="worker_status",
            arguments={
                "worker_id": started.data["worker_id"],
                "wait_seconds": 5,
            },
        )
    )
    assert delivered.data["status"] == ("completed" if parent_verifies else "stopped")
    assert delivered.data["verification"]["state"] == "missing"
    assert parent.state.task.prompt == prompt
    assert parent.state.task.require_verification == parent_verifies
    assert parent.state.changed_paths == ["calc.py"]
    await parent.step(ToolCall(name="read_file", arguments={"path": "calc.py"}))
    if parent_verifies:
        assert not parent.finalize("Delivered.").terminal
    assert parent.state.verification.state == "missing"
    result = await parent.step(
        ToolCall(
            name="run_tests",
            arguments={
                "command": "python -m pytest -q test_calc.py",
            },
        )
    )
    assert result.status == "success", result.content
    assert parent.finalize("Verified.").phase == "completed"
    await parent.workers.close()


@pytest.mark.asyncio
async def test_non_strict_parent_keeps_child_task_requirements(tmp_path):
    parent = Runtime(tmp_path, Settings(max_workers=1, completion="warn"), model=PausedModel())
    parent.start(TaskSpec(prompt="Implement and test"))
    result = await parent.workers.start("Implement and test", "worker", ["target.py"])
    child = parent.workers.workers[result["worker_id"]].runtime
    assert child.state.task.require_verification
    await parent.workers.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("delivery", ["automatic", "status", "resume"])
async def test_explore_can_report_a_fix_without_being_required_to_edit(tmp_path, delivery):
    (tmp_path / "target.py").write_text("def answer(): return 0\n")
    parent = Runtime(
        tmp_path,
        Settings(max_workers=1),
        model=CallThenFinish("read_file", {"path": "target.py"}),
    )
    parent.start(TaskSpec(prompt="Inspect the repository"))
    started = await parent.workers.start("Inspect target.py and recommend a fix and tests")
    worker = parent.workers.workers[started["worker_id"]]
    result = await worker.future
    assert result.phase == "completed" and result.observed
    assert result.task.require_observation
    assert not result.task.require_edit and not result.task.require_verification
    assert not result.changed_paths and worker.runtime.settings.read_only
    assert (tmp_path / "target.py").read_text() == "def answer(): return 0\n"
    if delivery == "status":
        await parent.step(ToolCall(name="worker_status", arguments={"worker_id": worker.worker_id}))
    elif delivery == "resume":
        parent.checkpoint()
        restored = Runtime(tmp_path, parent.settings, model=parent.model)
        restored.resume(parent.state.run_id)
        assert restored.state.team_usage == parent.state.team_usage
        parent = restored
    else:
        # UI polling does not deliver a tool result to the parent model.
        await parent.workers.status(worker.worker_id)
    parent.prepare_request()
    prompt, _, _ = parent.prepare_request()
    assert "Finished." in json.dumps(prompt)
    notifications = [row for row in parent.evidence.events(parent.state.run_id)
                     if row["event"] == "worker_handoff_delivered"]
    assert len(notifications) == (0 if delivery == "status" else 1)
    await parent.workers.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_writer", [False, True])
async def test_worker_writer_excludes_other_writers_and_parent_effects(tmp_path, cancel_writer):
    model = PausedModel()
    parent = Runtime(tmp_path, Settings(approval="auto", max_workers=2), model=model)
    parent.start(TaskSpec(prompt="demo"))
    started = await parent.workers.start("demo", "worker", ["src"])
    assert started["status"] == "running"
    await model.entered.wait()
    assert (await parent.workers.start("demo", "worker", ["src/core"]))[
        "reason"
    ] == "worker_scope_busy"
    assert (await parent.workers.start("demo", "worker", ["tests"]))["reason"] == "worker_scope_busy"
    reader = await parent.workers.start("Inspect the repository")
    assert reader["status"] == "running"
    write = {"path": "parent.txt", "content": "parent"}
    for call in [
        ToolCall(name="write_file", arguments=write),
        ToolCall(name="run_shell", arguments={"command": "touch parent.txt"}),
        ToolCall(name="run_tests", arguments={"command": "python -m pytest"}),
    ]:
        assert (await parent.step(call)).reason == "worker_writer_active"
    assert not (tmp_path / "parent.txt").exists()
    assert (await parent.step(ToolCall(name="list_files"))).status == "success"
    if cancel_writer:
        await parent.workers.stop(started["worker_id"])
    else:
        model.release.set()
        await parent.workers.workers[started["worker_id"]].future
    assert (await parent.step(ToolCall(name="write_file", arguments=write))).status == "success"
    await parent.workers.stop(reader["worker_id"])
    assert (await parent.workers.start("demo", "worker", ["tests"]))["status"] == "running"
    await parent.workers.close()


@pytest.mark.asyncio
async def test_cli_stdin_partial_line_is_cancellable_and_buffered(monkeypatch):
    read_fd, write_fd = os.pipe()
    with os.fdopen(read_fd) as stream:
        monkeypatch.setattr(sys, "stdin", stream)
        terminal = Terminal(Settings())
        answer = asyncio.create_task(terminal.question("Choice?"))
        try:
            os.write(write_fd, b"part")
            await asyncio.sleep(0.02)
            assert not answer.done()
            answer.cancel()
            with pytest.raises(asyncio.CancelledError):
                await answer
            os.write(write_fd, b"ial\nnext\n")
            assert await terminal.question("Again?") == "partial"
            assert await terminal.question("Another?") == "next"
        finally:
            os.close(write_fd)


@pytest.mark.asyncio
async def test_compound_shell_cannot_bypass_hard_deny_with_human_approval(tmp_path):
    (tmp_path / "keep.txt").write_text("keep")
    runtime = Runtime(
        tmp_path,
        Settings(
            approval="ask",
            rules=[ExecRule(prefix=["rm"], decision="deny", reason="no_delete")],
        ),
        approval_callback=lambda *_: True,
    )
    runtime.start(TaskSpec(prompt="demo"))
    result = await runtime.step(
        ToolCall(name="run_shell", arguments={"command": "rm keep.txt; echo done"})
    )
    assert result.status == "blocked"
    assert result.reason == "compound_shell_with_deny_rules"
    assert (tmp_path / "keep.txt").read_text() == "keep"
