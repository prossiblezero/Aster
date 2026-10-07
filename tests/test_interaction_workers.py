"""Exercise human decisions and workers through the real Runtime boundary."""

import asyncio
import json
import os
import shutil
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
    parent.start(TaskSpec(prompt="demo: preserve reproduction condition iso8859-1"))
    prompt, tools, report = parent.prepare_request()
    assert "Worker capability:" in prompt[0]["content"]
    assert "worker_start" in {t["name"] for t in tools}
    catalog = next(t for t in tools if t["name"] == "tool_load")
    assert "worker_start" not in catalog["parameters"]["properties"]["names"]["items"]["enum"]
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
    assert worker.runtime.state.task.max_steps <= parent.state.task.max_steps
    assert worker.runtime.state.task.max_turns <= parent.state.task.max_turns
    assert worker.runtime.settings.read_only and worker.runtime.settings.max_workers == 0
    assert "Also check the license." in json.dumps(model.requests[-1])
    assert "Explain the license finding." in json.dumps(model.requests[-1])
    assert "reproduction condition iso8859-1" in model.requests[-1][1]["content"]
    assert parent.state.team_usage["model_calls"] == previous_usage["model_calls"] + 1
    assert (await parent.workers.message(worker_id, "Again", resume=True))["continued"]
    await worker.future
    assert (await parent.workers.start("another task"))["status"] == "running"
    await parent.workers.close()


@pytest.mark.asyncio
async def test_saved_child_followups_preserve_history_budget_and_branch_boundaries(tmp_path):
    target = tmp_path / "module.py"
    target.write_text("answer = 42\n")
    model = CallThenFinish("read_file", {"path": "module.py"})
    settings = Settings(max_workers=0, completion="off")
    parent = Runtime(tmp_path, settings, model=model)
    parent.start(TaskSpec(prompt="Investigate module.py"))
    assert "worker_status" not in {tool["name"] for tool in parent.tool_definitions()}
    parent.finalize("Ready for explicit investigations.")
    before_child_entry = parent.session_tree.cursor
    started = await parent.workers.start("Read module.py; remember the answer", explicit=True)
    worker_id = started["worker_id"]
    await parent.workers.workers[worker_id].future
    assert parent._unavailable_reason("worker_start") == "workers_disabled"
    assert not parent._unavailable_reason("worker_status")
    assert "worker_status" in {tool["name"] for tool in parent.tool_definitions()}
    loader = next(tool for tool in parent.tool_definitions() if tool["name"] == "tool_load")
    assert "worker_status" not in loader["parameters"]["properties"]["names"]["items"]["enum"]
    before = parent.state.team_usage["model_calls"]
    parent_id = parent.state.run_id
    old_child = parent.workers.overview()[0]["run_id"]
    parent.checkpoint()

    restored = Runtime(tmp_path, settings, model=model)
    restored.resume(parent_id)
    assert "worker_status" in {tool["name"] for tool in restored.tool_definitions()}
    assert not restored.workers.workers  # Listing never starts background work.
    assert restored.workers.overview()[0]["status"] == "completed"
    target.write_text("answer = 43\n")
    assert restored.workers.overview()[0]["stale_paths"] == ["module.py"]
    for question in ("Explain the finding", "Which assumption might have changed?"):
        assert (await restored.workers.message(worker_id, question, resume=True))["continued"]
        await restored.workers.workers[worker_id].future
    assert restored.state.team_usage["model_calls"] == before + 2
    assert "Read module.py; remember the answer" in json.dumps(model.requests[-1])
    assert "Explain the finding" in json.dumps(model.requests[-1])
    assert "workspace edits" in json.dumps(model.requests[-1])
    assert restored.workers.overview()[0]["run_id"] != old_child
    # A new parent turn retains the child's handle and pending delivery on this branch.
    restored.continue_task("Integrate the investigations", adopt_workspace=True)
    restored.prepare_request()
    assert not restored.state.pending_handoffs
    assert restored.workers.overview()[0]["worker_id"] == worker_id
    assert not restored.workers.workers
    restored.state.team_usage["model_calls"] = restored.state.task.max_turns
    assert (await restored.workers.message(worker_id, "Beyond budget", resume=True))["status"] == "blocked"
    restored.finish("test_boundary")
    restored.checkout(before_child_entry, adopt_workspace=True)
    assert restored.workers.overview() == []
    assert "worker_status" not in {tool["name"] for tool in restored.tool_definitions()}
    restored.new_session()
    assert restored.workers.overview() == []


@pytest.mark.asyncio
async def test_child_restore_rejects_uncertain_effects_and_changed_permissions(tmp_path):
    parent = Runtime(tmp_path, Settings(max_workers=1, completion="off"),
                     model=CallThenFinish("list_files", {}))
    parent.start(TaskSpec(prompt="Investigate"))
    started = await parent.workers.start("Inspect files")
    worker_id = started["worker_id"]
    await parent.workers.workers[worker_id].future
    restored = Runtime(tmp_path, parent.settings, model=parent.model)
    restored.resume(parent.state.run_id)
    restored.state.mode = "plan"
    blocked = await restored.workers.message(worker_id, "Continue", resume=True)
    assert blocked["reason"] == "worker_restore_blocked"
    assert "permissions_changed" in blocked["detail"]
    restored.state.mode = "work"
    child = restored.state.worker_sessions[worker_id]["run_id"]
    marker = restored.evidence.path("runs", child, "inflight.json")
    marker.write_text(json.dumps({"calls": [{"call_id": "uncertain"}]}))
    blocked = await restored.workers.message(worker_id, "Continue", resume=True)
    assert "interrupted_tool_batch" in blocked["detail"]
    assert not restored.workers.workers


@pytest.mark.asyncio
async def test_parent_history_pins_child_messages_and_observation_access(tmp_path):
    requests = []
    class Echo:
        async def complete(self, messages, _tools):
            requests.append(json.dumps(messages))
            return ModelReply(text="FUTURE_SECRET" if "FUTURE_SECRET" in requests[-1] else "Earlier findings only.")

    parent = Runtime(tmp_path, Settings(completion="off", auto_dream=False), model=Echo())
    parent.start(TaskSpec(prompt="Original investigation"))
    parent.finalize("Ready for investigations.")
    started = await parent.workers.start("Investigate the original request", explicit=True)
    worker_id = started["worker_id"]
    worker = parent.workers.workers[worker_id]
    earlier = worker.runtime.evidence.archive_observation(worker.runtime.state.run_id, "earlier", "run_shell", "Earlier evidence")
    worker.runtime.checkpoint()
    parent.workers._remember(worker)
    parent.checkpoint()
    boundary = parent.session_tree.cursor
    probe = Runtime(tmp_path, worker.runtime.settings, model=parent.model)
    with pytest.raises(ValueError, match="session_snapshot_identity_mismatch"):
        probe.resume("different-run", entry_id=worker.runtime.session_tree.cursor)
    probe.resume(worker.runtime.state.run_id, entry_id=worker.runtime.session_tree.cursor)
    with pytest.raises(RuntimeError, match="snapshot_requires_continuation"):
        await probe.step(ToolCall(name="observation_read", arguments={}))
    await parent.workers.message(worker_id, "FUTURE_SECRET")
    future = worker.runtime.evidence.archive_observation(worker.runtime.state.run_id, "later", "run_shell", "FUTURE_TOOL_OUTPUT")
    await worker.future
    assert "FUTURE_SECRET" in parent.workers.overview()[0]["answer"]

    parent.checkout(boundary)
    assert "FUTURE_SECRET" not in json.dumps(parent._structured_history)
    assert "FUTURE_SECRET" not in parent.workers.overview()[0].get("answer", "")
    before = parent.state.team_usage["model_calls"]
    checkpoint = worker.runtime.evidence.path("runs", worker.runtime.state.run_id, "checkpoint.json")
    latest = checkpoint.read_bytes()
    probe = Runtime(tmp_path, worker.runtime.settings, model=parent.model)
    probe.resume(worker.runtime.state.run_id, entry_id=parent.state.worker_sessions[worker_id]["entry_id"])
    probe.checkpoint()
    assert checkpoint.read_bytes() == latest
    parent.state.team_usage["model_calls"] = parent.state.task.max_turns
    assert (await parent.workers.message(worker_id, "Over budget", resume=True))["status"] == "blocked"
    await parent.workers.close()
    assert checkpoint.read_bytes() == latest
    parent.state.team_usage["model_calls"] = before
    assert (await parent.workers.message(worker_id, "Explain the earlier findings", resume=True))["continued"]
    restored = parent.workers.workers[worker_id]
    await restored.future
    assert "FUTURE_SECRET" not in requests[-1] and "FUTURE_TOOL_OUTPUT" not in requests[-1]
    assert parent.state.team_usage["model_calls"] == before + 1
    restored.runtime.continue_task("Inspect available evidence", adopt_workspace=True)
    for observation, expected in ((earlier, "success"), (future, "blocked")):
        result = await restored.runtime.step(ToolCall(name="observation_read", arguments={"observation_id": observation["id"]}))
        assert result.status == expected
    listing = await restored.runtime.step(ToolCall(name="observation_read", arguments={}))
    assert future["id"] not in listing.content and earlier["id"] in listing.content
    await parent.workers.close()
    parent.finish("test_boundary")
    parent.checkout(boundary)
    parent.state.worker_sessions[worker_id].pop("entry_id")
    assert parent.workers.overview()[0]["reason"] == "worker_snapshot_unavailable"
    assert (await parent.workers.message(worker_id, "Do not guess history", resume=True))["reason"] == "worker_snapshot_unavailable"


@pytest.mark.asyncio
async def test_guidance_during_final_response_is_not_lost(tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()
    requests = []

    class Model:
        async def complete(self, messages, _tools):
            requests.append(messages)
            if len(requests) == 1:
                entered.set()
                await release.wait()
            return ModelReply(text="Finding delivered.")

    parent = Runtime(tmp_path, Settings(max_workers=1, completion="off"), model=Model())
    parent.start(TaskSpec(prompt="Gather evidence"))
    started = await parent.workers.start("Investigate", explicit=True)
    worker_id = started["worker_id"]
    await entered.wait()
    assert not parent.finalize("Too early").terminal
    assert (await parent.workers.message(worker_id, "Also address cancellation"))["status"] == "queued"
    release.set()
    await parent.workers.workers[worker_id].future
    assert len(requests) == 2 and "Also address cancellation" in json.dumps(requests[-1])
    assert not parent.state.worker_sessions[worker_id]["pending_messages"]
    parent.prepare_request()
    assert parent.finalize("All findings received").terminal


@pytest.mark.asyncio
@pytest.mark.parametrize("wait_for", ["any", "all"])
async def test_worker_status_waits_without_cancelling_child(tmp_path, wait_for):
    model = PausedModel()
    parent = Runtime(tmp_path, Settings(max_workers=1, completion="off"), model=model)
    parent.start(TaskSpec(prompt="demo"))
    started = await parent.workers.start("demo")
    worker_id = started["worker_id"]
    await model.entered.wait()

    timed_out = await parent.workers.status(worker_id, wait_seconds=0.01, wait_for=wait_for)
    assert timed_out["status"] == "running"
    assert not parent.workers.workers[worker_id].future.cancelled()

    waiter = asyncio.create_task(parent.workers.status(worker_id, wait_seconds=60, wait_for=wait_for))
    await asyncio.sleep(0)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert not parent.workers.workers[worker_id].future.done()

    model.release.set()
    finished = await parent.workers.status(worker_id, wait_seconds=1, wait_for=wait_for)
    assert finished["status"] == "completed"
    await parent.workers.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("wait_for", ["any", "all"])
async def test_worker_status_uses_one_deadline_and_preserves_cancellation(tmp_path, wait_for):
    model = PausedModel()
    parent = Runtime(tmp_path, Settings(max_workers=2, completion="off"), model=model)
    parent.start(TaskSpec(prompt="demo"))
    first = (await parent.workers.start("demo"))["worker_id"]
    second = (await parent.workers.start("demo"))["worker_id"]
    await model.entered.wait()
    ids = [first, "missing", second, first]
    partial = await parent.workers.status(ids, wait_seconds=0.01, wait_for=wait_for)
    assert partial["status"] == "running"
    assert [r["worker_id"] for r in partial["workers"]] == ids[:3]
    assert partial["workers"][1]["status"] == "missing"
    waiting = asyncio.create_task(parent.workers.status(ids, wait_seconds=300, wait_for=wait_for))
    await asyncio.sleep(0)
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    assert all(not w.future.done() for w in parent.workers.workers.values())
    await parent.workers.stop(second)
    model.release.set()
    await parent.workers.workers[first].future
    outcome = await parent.step(
        ToolCall(
            name="worker_status",
            arguments={"worker_id": ids, "wait_seconds": 300, "wait_for": wait_for},
        )
    )
    assert outcome.status == "success" and outcome.data["status"] == "finished"
    assert [r["status"] for r in outcome.data["workers"]] == ["completed", "missing", "cancelled"]
    prompt, _, _ = parent.prepare_request()
    assert "Worker handoff (untrusted evidence" not in json.dumps(prompt)
    assert all(worker.delivered for worker in parent.workers.workers.values())
    assert not parent.state.terminal
    await parent.workers.close()


@pytest.mark.parametrize("wait_for", [None, "all"])
async def test_worker_status_waits_for_requested_completions(tmp_path, wait_for):
    fast, slow = PausedModel(), PausedModel()
    parent = Runtime(tmp_path, Settings(max_workers=2, completion="off"), model=fast)
    parent.start(TaskSpec(prompt="Inspect two independent modules"))
    first = (await parent.workers.start("Inspect first module"))["worker_id"]
    second = (await parent.workers.start("Inspect second module"))["worker_id"]
    parent.workers.workers[second].runtime.model = slow
    await asyncio.gather(fast.entered.wait(), slow.entered.wait())

    def query():
        return parent.step(ToolCall(name="worker_status", arguments={
            "worker_id": [first, second], "wait_seconds": 300,
            **({"wait_for": wait_for} if wait_for else {}),
        }))

    try:
        waiting = asyncio.create_task(query())
        await asyncio.sleep(0)
        fast.release.set()
        await parent.workers.workers[first].future
        await asyncio.sleep(0)
        if wait_for == "all":
            assert not waiting.done(), "all must keep waiting after the first completion"
            assert first in parent.state.pending_handoffs
        else:
            result = await asyncio.wait_for(waiting, 2)
            assert result.data["status"] == "running"
            assert [item["status"] for item in result.data["workers"]] == ["completed", "running"]
            assert first not in parent.state.pending_handoffs
            assert second in parent.state.pending_handoffs
            assert not parent.finalize("Only one result reviewed").terminal
            waiting = asyncio.create_task(query())
            await asyncio.sleep(0)
            assert not waiting.done(), "An already consumed result must not end the next wait"
        assert not parent.workers.workers[second].future.done()
        slow.release.set()
        result = await asyncio.wait_for(waiting, 2)
        assert result.data["status"] == "finished"
        assert not parent.state.pending_handoffs
        assert parent.finalize("Both results reviewed").phase == "completed"
    finally:
        await parent.workers.close()


@pytest.mark.parametrize("saved", ["live", "restored", "unavailable"])
async def test_worker_status_delivers_already_ready_result_once(tmp_path, saved):
    model = PausedModel()
    model.release.set()
    parent = Runtime(tmp_path, Settings(max_workers=2, completion="off"), model=model)
    parent.start(TaskSpec(prompt="Inspect modules"))
    first = (await parent.workers.start("Inspect first module"))["worker_id"]
    await parent.workers.workers[first].future
    if saved != "live":
        original = parent
        parent = Runtime(tmp_path, original.settings, model=model)
        parent.resume(original.state.run_id)
        await original.workers.close()
        if saved == "unavailable":
            parent.state.worker_sessions[first].pop("entry_id")
    slow = PausedModel()
    second = (await parent.workers.start("Inspect second module"))["worker_id"]
    parent.workers.workers[second].runtime.model = slow
    await slow.entered.wait()
    try:
        snapshot = await asyncio.wait_for(parent.workers.status([first, second], 300), 2)
        assert snapshot["status"] == "running"
        assert snapshot["workers"][0]["status"] == ("unavailable" if saved == "unavailable" else "completed")
        assert first in parent.state.pending_handoffs  # UI polling does not consume delivery.
        immediate = await parent.workers.status([first, second], wait_for="all")
        assert immediate == snapshot  # Zero seconds is a snapshot even for all.
        partial = await parent.workers.status([first, second], 0.01, wait_for="all")
        assert partial == snapshot and not parent.workers.workers[second].future.done()
        waiting_all = asyncio.create_task(parent.workers.status([first, second], 300, wait_for="all"))
        await asyncio.sleep(0)
        assert not waiting_all.done(), "An unread ready result must not release an all waiter"
        waiting_all.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting_all
        assert first in parent.state.pending_handoffs
        assert not parent.workers.workers[second].future.done()
        result = await asyncio.wait_for(parent.step(ToolCall(name="worker_status", arguments={
            "worker_id": [first, second], "wait_seconds": 300,
        })), 2)
        assert result.status == "success" and first not in parent.state.pending_handoffs
        prompt, _, _ = parent.prepare_request()
        assert "Worker handoff (untrusted evidence" not in json.dumps(prompt)
        waiting = asyncio.create_task(parent.workers.status([first, second], 300))
        await asyncio.sleep(0)
        assert not waiting.done()
        await parent.workers.stop(second)
        result = await asyncio.wait_for(waiting, 2)
        assert result["status"] == "finished"
        assert result["workers"][1]["status"] == "cancelled"
    finally:
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


@pytest.mark.parametrize("boundary", ["sequential", "parallel", "invalid_batch", "turn", "team_tools", "team_tools_sequential"])
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
        max_workers=1, completion="off", read_parallelism=1 if "sequential" in boundary else 2,
    ), model=Model())
    parent.start(TaskSpec(prompt="Inspect the repository"))
    started = await parent.workers.start("Inspect target.py")
    worker = parent.workers.workers[started["worker_id"]]
    worker.runtime.state.task.max_steps = 12 if boundary == "turn" else 2
    worker.runtime.state.task.max_turns = 2 if boundary == "turn" else 12
    if boundary.startswith("team_tools"):
        parent.state.task.max_steps = 4
        worker.runtime.state.task.max_steps = 12
    result = await asyncio.wait_for(worker.future, 5)
    delivered = await parent.workers.status(worker.worker_id)
    assert len(requests) == 2 and requests[0][1] and requests[1][1] == []
    assert "PARTIAL handoff" in json.dumps(requests[1][0])
    assert result.step == (1 if boundary == "turn" else 2)
    assert result.phase == "stopped" and delivered["delivery"] == "partial"
    assert result.stop_reason == (
        "team_tool_calls_limit" if boundary.startswith("team_tools") else
        "turn_limit" if boundary == "turn" else "step_limit"
    )
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
            deliver = not tools or "only final delivery is permitted" in json.dumps(messages)
            return ModelReply(
                calls=[ToolCall(name="read_file", arguments={"path": "target.py"})
                       for _ in range(6 if limit == "tool_calls" else 1)] if not deliver else [],
                text="Partial findings." if deliver else "", usage={"total_tokens": 7},
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
        # A tight budget can rebuild the first request as a smaller partial handoff.
        billed_tokens = first.runtime._request_estimate
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
    # The last model call now delivers an answer; satisfied requirements can complete.
    assert parent.state.stop_reason == (
        "verified" if limit == "model_calls" else f"team_{'token' if limit == 'tokens' else limit}_limit"
    )
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
async def test_worker_handoff_carries_line_anchored_source_evidence(tmp_path):
    (tmp_path / "module.py").write_text("first = 1\nanswer = 42\nlast = 3\n")

    class Model:
        def __init__(self):
            self.requests = []

        async def complete(self, messages, tools):
            self.requests.append((messages, tools))
            if len(self.requests) == 1:
                return ModelReply(calls=[ToolCall(name="read_file", arguments={
                    "path": "module.py", "start": 2, "end": 2,
                })])
            return ModelReply(text="answer is 42")

    model = Model()
    parent = Runtime(tmp_path, Settings(max_workers=1, completion="strict"), model=model)
    parent.start(TaskSpec(prompt="Investigate module.py", require_observation=True))
    started = await parent.workers.start("Read module.py and report the answer")
    worker = parent.workers.workers[started["worker_id"]]
    await worker.future
    status = await parent.workers.status(worker.worker_id)
    excerpt = status["source_excerpts"][0]
    assert excerpt["path"] == "module.py"
    assert excerpt["start"] == excerpt["end"] == 2
    assert excerpt["content"] == "answer = 42"
    assert not parent.state.observed  # UI polling must not consume model evidence.
    prompt, _, _ = parent.prepare_request()
    rendered = json.dumps(prompt, ensure_ascii=False)
    assert "Worker handoff" in rendered and "answer = 42" in rendered
    assert "2: answer = 42" in rendered
    assert "source_sha256" not in rendered
    assert len(excerpt["source_sha256"]) == 64
    assert parent.state.observed and not parent.state.read_evidence
    assert parent.state.verification.state == "missing"
    assert parent.state.step == 0
    assert parent.finalize("Delegated finding: module.py:2 sets answer to 42.").phase == "completed"
    await parent.workers.close()


@pytest.mark.asyncio
async def test_read_file_history_includes_line_metadata_without_changing_file_content(tmp_path):
    (tmp_path / "module.py").write_text("first = 1\nanswer = 42\n")
    runtime = Runtime(tmp_path, Settings(completion="off"), model=CallThenFinish("read_file", {}))
    runtime.start(TaskSpec(prompt="Inspect module.py"))
    result = await runtime.step(ToolCall(name="read_file", arguments={
        "path": "module.py", "start": 2, "end": 2,
    }))
    assert result.content == "answer = 42"
    output = next(item for item in runtime._structured_history
                  if item.get("type") == "function_call_output")
    payload = json.loads(output["output"])
    assert payload["content"] == "2: answer = 42"
    assert payload["start"] == payload["end"] == 2
    assert payload["evidence_id"] == result.evidence_id and "source_ref" not in payload
    assert (tmp_path / "module.py").read_text() == "first = 1\nanswer = 42\n"


@pytest.mark.parametrize("delivery", ["auto", "status", "saved"])
async def test_worker_evidence_is_snapshot_pinned_and_never_edit_authority(tmp_path, delivery):
    target = tmp_path / "module.py"
    target.write_text("answer = 42\n")
    settings = Settings(max_workers=1, approval="auto", max_final_revisions=0)
    parent = Runtime(tmp_path, settings, model=CallThenFinish("read_file", {"path": "module.py"}))
    parent.start(TaskSpec(prompt="Inspect module.py"))
    started = await parent.workers.start("Inspect module.py")
    worker = parent.workers.workers[started["worker_id"]]
    await worker.future
    boundary = parent.session_tree.cursor
    # A later child checkpoint must not leak into the saved parent snapshot.
    worker.runtime.state.working_memory["module.py"]["summary"] = "FUTURE_SOURCE"
    worker.runtime.checkpoint()
    if delivery == "saved":
        parent = Runtime(tmp_path, settings, model=parent.model)
        parent.resume(parent_run := worker.runtime.state.parent_run_id, entry_id=boundary)
        assert parent.state.run_id == parent_run
    else:
        worker.runtime.state.working_memory["module.py"]["summary"] = "answer = 42"
    if delivery == "status":
        await parent.step(ToolCall(name="worker_status", arguments={"worker_id": worker.worker_id}))
    else:
        prompt, _, _ = parent.prepare_request()
        assert "FUTURE_SOURCE" not in json.dumps(prompt)
    assert parent.state.observed
    if delivery == "saved":
        parent.continue_task()
    blocked = await parent.step(ToolCall(name="patch_file", arguments={
        "path": "module.py", "old_text": "42", "new_text": "99",
    }))
    assert blocked.reason == "fresh_read_required"
    assert parent.state.verification.state == "missing"
    await parent.workers.close()

    stale = Runtime(tmp_path, settings, model=parent.model)
    target.write_text("answer = 99\n")
    stale.resume(parent_run_id := worker.runtime.state.parent_run_id, entry_id=boundary, adopt_workspace=True)
    assert stale.state.run_id == parent_run_id
    status = stale.workers.overview()[0]
    assert status["stale_paths"] == ["module.py"]
    assert status["source_excerpts"][0]["content"] == "answer = 42"
    stale.prepare_request()
    assert not stale.state.observed and not stale.verifier.final(stale.state).passed


async def test_autonomous_worker_and_late_delivery_block_premature_final(tmp_path):
    model = PausedModel()
    parent = Runtime(tmp_path, Settings(max_workers=1, completion="off"), model=model)
    parent.start(TaskSpec(prompt="Inventory"))
    started = await parent.workers.start("List files")
    worker = parent.workers.workers[started["worker_id"]]
    await model.entered.wait()
    assert not parent.finalize("Premature").terminal
    assert not worker.future.cancelling()
    model.release.set()
    await worker.future
    assert not parent.finalize("Did not see the new handoff").terminal
    assert "after your last observation" in parent.state.feedback
    parent.prepare_request()
    assert parent.finalize("Reviewed the handoff").phase == "completed"
    await parent.workers.close()


async def test_token_exhausted_followup_does_not_replace_child_snapshot(tmp_path):
    parent = Runtime(tmp_path, Settings(max_workers=1, output_tokens=128, completion="off"),
                     model=CallThenFinish("list_files", {}))
    parent.start(TaskSpec(prompt="Inventory", max_tokens=50000))
    started = await parent.workers.start("List files")
    worker = parent.workers.workers[started["worker_id"]]
    await worker.future
    record = dict(parent.state.worker_sessions[worker.worker_id])
    parent.state.team_usage["charged_tokens"] = parent.state.task.max_tokens
    result = await parent.workers.message(worker.worker_id, "Follow up", resume=True)
    assert result["reason"] == "worker_continuation_unavailable"
    assert parent.state.worker_sessions[worker.worker_id] == record
    assert worker.runtime.state.run_id == record["run_id"]
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
    todo = await parent.step(ToolCall(name="todo_add", arguments={
        "content": "Implement answer()", "acceptance": "The public answer() returns 42.",
    }))
    started = await parent.step(
        ToolCall(
            name="worker_start",
            arguments={
                "task": "Implement answer() = 42 in calc.py; parent integration tests cover it.",
                "mode": "worker",
                "write_scope": ["calc.py"],
                "todo_id": todo.data["id"],
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
    reviewed = await parent.step(ToolCall(name="todo_update", arguments={
        "todo_id": todo.data["id"], "status": "done",
        "resolution": "test_calc.py called answer() and passed after integration.",
    }))
    assert reviewed.status == "success"
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


@pytest.mark.parametrize("completion", ["strict", "warn", "off"])
async def test_owned_requirement_survives_rejected_start_delivery_and_parent_resume(tmp_path, completion):
    model = PausedModel()
    settings = Settings(max_workers=1, completion=completion, max_final_revisions=0)
    parent = Runtime(tmp_path, settings, model=model)
    parent.start(TaskSpec(prompt="Inspect the project and report its files."))
    for content in ("List files", "Review the final list"):
        assert (await parent.step(ToolCall(name="todo_add", arguments={
            "content": content, "acceptance": "Confirm the actual workspace file list.",
        }))).status == "success"
    started = await parent.workers.start("List the workspace files", todo_id="1")
    worker = parent.workers.workers[started["worker_id"]]
    await model.entered.wait()
    assert (await parent.workers.start("Duplicate", todo_id="1"))["reason"] == "todo_unavailable"
    assert (await parent.workers.start("Review", todo_id="2"))["reason"] == "worker_limit"
    assert parent.state.todos[1]["status"] == "pending"
    assert not parent.finalize("Too early").terminal
    assert not worker.future.done() and not worker.future.cancelling()
    assert (await parent.step(ToolCall(name="todo_update", arguments={
        "todo_id": "1", "status": "done", "resolution": "Unsubstantiated early claim",
    }))).reason == "todo_worker_running"
    model.release.set()
    await worker.future
    assert parent.state.todos[0]["status"] == "pending"
    assert parent.state.todos[0]["delivery"] == "complete"
    assert "requirement" in parent.state.feedback.casefold()
    restored = Runtime(tmp_path, settings, model=model)
    restored.resume(parent.state.run_id)
    prompt, _, _ = restored.prepare_request()
    assert "Assigned requirement" in worker.runtime.state.task.prompt
    assert '"todo_id": "1"' in json.dumps(prompt).replace('\\"', '"')
    assert restored.state.todos[0]["status"] == "pending"
    await restored.step(ToolCall(name="list_files"))
    assert (await restored.step(ToolCall(name="todo_update", arguments={
        "todo_id": "1", "status": "done",
    }))).reason == "todo_resolution_required"
    for todo_id in ("1", "2"):
        result = await restored.step(ToolCall(name="todo_update", arguments={
            "todo_id": todo_id, "status": "done", "resolution": "Reviewed the observed empty file list.",
        }))
        assert result.status == "success"
    assert restored.finalize("Reviewed the file list").phase == "completed"
    await parent.workers.close()
    await restored.workers.close()


async def test_partial_requirement_can_be_reassigned_without_stale_delivery_overwrite(tmp_path):
    model = PausedModel()
    parent = Runtime(tmp_path, Settings(max_workers=1), model=model)
    parent.start(TaskSpec(prompt="demo"))
    await parent.step(ToolCall(name="todo_add", arguments={
        "content": "Inventory", "acceptance": "List actual files.",
    }))
    first = await parent.workers.start("Inventory", todo_id="1")
    old = await parent.workers.stop(first["worker_id"])
    assert parent.state.todos[0]["delivery"] == "partial"
    assert parent.state.todos[0]["status"] == "pending"
    second = await parent.workers.start("Inventory", todo_id="1")
    worker = parent.workers.workers[second["worker_id"]]
    parent.workers._record_delivery(old)
    assert parent.state.todos[0]["delivery"] == "running"
    assert parent.state.todos[0]["worker_run_id"] == worker.runtime.state.run_id
    assert (await parent.workers.message(first["worker_id"], "Retry", resume=True))[
        "reason"
    ] == "todo_unavailable"
    await parent.workers.stop(second["worker_id"])
    # Continuing a checkpoint must not carry an orphaned running ownership lease.
    parent.state.todos[0]["delivery"] = "running"
    parent.finish("user_abort")
    parent.continue_task("Continue the inventory")
    assert parent.state.todos[0]["delivery"] == "interrupted"
    assert parent.state.todos[0]["status"] == "pending"
    await parent.workers.close()


async def test_requirement_review_also_applies_to_solo_and_new_workers_preserve_tail_budget(tmp_path):
    parent = Runtime(tmp_path, Settings(max_workers=1, output_tokens=128), model=PausedModel())
    parent.start(TaskSpec(prompt="demo", max_tokens=10000))
    await parent.step(ToolCall(name="todo_add", arguments={
        "content": "Finish the requested work", "acceptance": "Check the actual result.",
    }))
    parent._request_estimate = 2200
    parent.state.team_usage["charged_tokens"] = 6000
    assert (await parent.workers.start("Inspect", todo_id="1"))[
        "reason"
    ] == "team_budget_reserved_for_parent"
    assert not parent.workers.workers and parent.state.todos[0]["status"] == "pending"
    assert not parent.finalize("Pretend complete").terminal
    assert parent.verifier.final(parent.state).reason == "requirements_unreviewed"
    reviewed = await parent.step(ToolCall(name="todo_update", arguments={
        "todo_id": "1", "status": "done", "resolution": "Inspected the result directly.",
    }))
    assert reviewed.status == "success"
    assert parent.finalize("Done").phase == "completed"
    parent.start(TaskSpec(prompt="A new request", max_tokens=10000))
    assert parent._request_estimate == 0
    await parent.step(ToolCall(name="todo_add", arguments={
        "content": "An unfinished requirement", "acceptance": "Check the actual result.",
    }))
    await parent.step(ToolCall(name="todo_update", arguments={
        "todo_id": "1", "status": "cancelled", "resolution": "Budget exhausted.",
    }))
    state = parent.finalize("Partial result")
    assert state.phase == "completed" and state.stop_reason == "completed_with_warning"
    assert "Cancelled requirements (not verified)" in state.final
    assert not parent.verifier.final(state).passed
    parent.continue_task()
    assert parent.finalize("Still partial").stop_reason == "completed_with_warning"
    parent.continue_task("What is 2 + 2?")
    state = parent.finalize("4")
    assert state.stop_reason == "verified" and state.final == "4"
    assert not state.cancelled_requirements


async def test_training_final_waits_for_owned_work_but_budget_still_terminates(tmp_path):
    from aster.schema import Final
    from aster.training.env import AsterEnv

    env = AsterEnv(tmp_path, Settings(max_workers=1, max_final_revisions=0))
    env.reset(TaskSpec(prompt="demo", max_turns=6))
    env.runtime.model = PausedModel()
    await env.step(ToolCall(name="todo_add", arguments={
        "content": "Inventory", "acceptance": "List actual files.",
    }))
    started = await env.runtime.workers.start("Inventory", todo_id="1")
    worker = env.runtime.workers.workers[started["worker_id"]]
    await env.runtime.model.entered.wait()
    _, _, terminated, truncated, _ = await env.step(Final(answer="Too early"))
    assert not terminated and not truncated and not worker.future.cancelling()
    for _ in range(3):
        await env.step(Final(answer="Still too early"))
    _, reward, terminated, truncated, _ = await env.step(Final(answer="At the budget"))
    assert truncated and not terminated and reward == -1
    assert worker.future.done() and env.runtime.state.todos[0]["delivery"] == "partial"
    env.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("delivery", ["automatic", "status", "resume"])
async def test_explore_can_report_a_fix_without_being_required_to_edit(tmp_path, delivery):
    (tmp_path / "target.py").write_text("def answer(): return 0\n")
    parent = Runtime(
        tmp_path,
        Settings(max_workers=1),
        model=CallThenFinish("read_file", {"path": "target.py"}),
    )
    original = "Fix non-ASCII hostname with iso8859-1; test SMTP and Message-ID."
    parent.start(TaskSpec(prompt=original))
    parent.add_message("user", "Unrelated private parent history.", important=True)
    assignment = "Inspect target.py and recommend a fix and tests"
    parent_work = "Inspect the independent SMTP module, then run integration tests."
    started = await parent.step(ToolCall(name="worker_start", arguments={
        "task": assignment, "parent_work": parent_work,
    }))
    worker = parent.workers.workers[started.data["worker_id"]]
    result = await worker.future
    child_prompt = parent.model.requests[0]
    assert child_prompt[1]["content"].count(original) == 1
    assert assignment in child_prompt[1]["content"]
    assert parent_work in child_prompt[1]["content"]
    assert "Unrelated private parent history." not in json.dumps(child_prompt)
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
    assert parent_work in json.dumps(prompt)
    assert parent.state.task.require_edit and parent.state.task.require_verification
    notifications = [row for row in parent.evidence.events(parent.state.run_id)
                     if row["event"] == "worker_handoff_delivered"]
    assert len(notifications) == (0 if delivery == "status" else 1)
    await parent.workers.close()


@pytest.mark.asyncio
async def test_worker_background_tracks_user_turn_through_resume_and_branch_checkout(tmp_path):
    class Capture:
        def __init__(self):
            self.requests = []

        async def complete(self, messages, _tools):
            self.requests.append(messages)
            return ModelReply(text="Subtask delivered.")

    model = Capture()
    settings = Settings(max_workers=1, completion="off", memory_enabled=False)
    root = tmp_path / "workspace"
    parent = Runtime(root, settings, model=model)
    original = "Original project requirement: preserve the file format."
    current = "Current failure: hostname 漢字 and iso-8859-1; SMTP must work."
    sibling = "SIBLING_ONLY: investigate the alternative UTF-8 requirement."
    synthetic = "SYNTHETIC_PRIVATE: a coordination record, not a user request."

    async def assert_background(runtime, expected, excluded):
        runtime.add_message("user", synthetic, important=True)
        started = await runtime.workers.start("Summarize the reproduction conditions.")
        worker = runtime.workers.workers[started["worker_id"]]
        await worker.future
        prompt = model.requests[-1][1]["content"]
        assert original in prompt and expected in prompt
        assert ("takes precedence over the original request" in prompt) == (expected != original)
        assert excluded not in json.dumps(model.requests[-1], ensure_ascii=False)
        assert synthetic not in json.dumps(model.requests[-1], ensure_ascii=False)
        assert runtime.state.task.prompt == original
        runtime.finalize("Done.")
        await runtime.workers.close()

    parent.start(TaskSpec(prompt=original))
    parent.finalize("Done.")
    parent.continue_task(current)
    await assert_background(parent, current, sibling)
    current_run = parent.state.run_id

    parent.continue_task(sibling)
    parent.finalize("Done.")
    sibling_entry = parent.session_tree.cursor

    restored = Runtime(root, settings, model=model)
    restored.resume(current_run)
    restored.continue_task()
    await assert_background(restored, current, sibling)

    restored.checkout(sibling_entry)
    restored.continue_task()
    await assert_background(restored, sibling, current)

    # Portable checkpoints may lack the optional session tree. Do not guess the
    # current request from user-role coordination records in imported history.
    portable = tmp_path / "portable"
    shutil.copytree(restored.evidence.path("runs"), portable / ".aster" / "runs")
    legacy = Runtime(portable, settings, model=model)
    legacy.resume(restored.state.run_id)
    legacy.continue_task()
    await assert_background(legacy, original, sibling)


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
