"""Running conversations accept durable guidance without replaying or tearing tools."""

import asyncio
import json

import pytest

from aster.config import Settings
from aster.core.runtime import Runtime
from aster.messages import history_groups
from aster.schema import ModelReply, TaskSpec, ToolCall
from aster.tools.registry import ToolRegistry

pytestmark = pytest.mark.core


async def test_external_policy_delivers_guidance_before_next_observation(tmp_path):
    runtime = Runtime(tmp_path, Settings(memory_enabled=False))
    runtime.start(TaskSpec(prompt="Inspect the workspace"))
    key = runtime.enqueue_user_message("Also explain the assumptions.")
    stale = await runtime.step(ToolCall(name="list_files"))
    assert stale.reason == "user_message_pending"
    prompt, _, _ = runtime.prepare_request()
    assert "Also explain the assumptions." in json.dumps(prompt)
    assert runtime.state.delivered_user_messages == [key] and not runtime.state.user_inbox
    assert (await runtime.step(ToolCall(name="list_files"))).status == "success"


@pytest.mark.parametrize("stale_tools", [False, True])
async def test_message_during_model_response_is_seen_before_more_effects(tmp_path, stale_tools):
    started, release = asyncio.Event(), asyncio.Event()
    requests = []

    class Model:
        async def complete(self, messages, tools):
            requests.append(json.dumps(messages))
            if len(requests) == 1:
                started.set()
                await release.wait()
                return ModelReply(calls=[ToolCall(name="write_file", arguments={
                    "path": "stale.txt", "content": "old plan",
                })]) if stale_tools else ModelReply(text="Old final answer")
            return ModelReply(text="Updated answer")

    runtime = Runtime(tmp_path, Settings(memory_enabled=False, approval="auto", completion="off"), model=Model())
    running = asyncio.create_task(runtime.run(TaskSpec(prompt="Original objective")))
    await started.wait()
    first = runtime.enqueue_user_message("Also explain the limits.")
    second = runtime.enqueue_user_message("Use Chinese in the answer.")
    release.set()
    state = await running
    assert state.phase == "completed" and state.final == "Updated answer"
    assert len(requests) == 2 and "Original objective" in requests[1]
    assert "Also explain the limits." in requests[1] and "Use Chinese" in requests[1]
    assert state.delivered_user_messages == [first, second] and not state.user_inbox
    assert not (tmp_path / "stale.txt").exists()
    history_groups(runtime._structured_history)


async def test_guidance_between_tools_finishes_current_write_and_skips_later_writes(tmp_path, monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    original = ToolRegistry.execute
    executed = []

    async def execute(registry, call):
        result = await original(registry, call)
        executed.append(call.arguments["path"])
        entered.set()
        await release.wait()
        return result

    monkeypatch.setattr(ToolRegistry, "execute", execute)
    responses = iter([
        ModelReply(calls=[ToolCall(name="write_file", arguments={"path": name, "content": name})
                          for name in ["first.txt", "second.txt"]]),
        ModelReply(text="The first operation completed; the remaining plan was updated."),
    ])

    class Model:
        async def complete(self, *_):
            return next(responses)

    runtime = Runtime(tmp_path, Settings(memory_enabled=False, approval="auto", completion="off"), model=Model())
    running = asyncio.create_task(runtime.run(TaskSpec(prompt="Carry out the task")))
    await entered.wait()
    runtime.enqueue_user_message("Stop after the current operation and report what happened.")
    release.set()
    state = await running
    assert state.phase == "completed" and executed == ["first.txt"]
    assert (tmp_path / "first.txt").read_text() == "first.txt" and not (tmp_path / "second.txt").exists()
    outputs = [item for item in runtime._structured_history if item.get("type") == "function_call_output"]
    assert len(outputs) == 2 and "user_message_pending" in outputs[1]["output"]
    history_groups(runtime._structured_history)


async def test_pending_messages_survive_checkpoint_gap_cancel_and_resume_once(tmp_path):
    settings = Settings(memory_enabled=False, completion="off")
    runtime = Runtime(tmp_path, settings)
    runtime.start(TaskSpec(prompt="Original objective"))
    runtime._in_turn = True
    key = runtime.enqueue_user_message("Please include the caveats.")
    runtime._in_turn = False
    # Simulate loss of the in-memory queue before the next settled checkpoint.
    restored = Runtime(tmp_path, settings)
    restored.resume(runtime.state.run_id)
    assert restored.state.user_inbox == [{"id": key, "content": "Please include the caveats."}]
    restored.finish("cancelled")
    restored.continue_task()

    class Model:
        async def complete(self, messages, _tools):
            assert "Please include the caveats." in json.dumps(messages)
            return ModelReply(text="With caveats")

    restored.model = Model()
    assert (await restored.run()).phase == "completed"
    reopened = Runtime(tmp_path, settings)
    reopened.resume(restored.state.run_id)
    assert reopened.state.delivered_user_messages == [key] and not reopened.state.user_inbox
    occurrences = [row for row in reopened._structured_history if row.get("content") == "Please include the caveats."]
    assert len(occurrences) == 1


async def test_guidance_during_approval_prevents_outdated_write(tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()

    async def approve(*_):
        entered.set()
        await release.wait()
        return True

    runtime = Runtime(tmp_path, Settings(memory_enabled=False), approval_callback=approve)
    runtime.start(TaskSpec(prompt="Original objective"))
    operation = asyncio.create_task(runtime.step(ToolCall(name="write_file", arguments={"path": "old.txt", "content": "x"})))
    await entered.wait()
    runtime.enqueue_user_message("Do not proceed with that file.")
    release.set()
    result = await operation
    assert result.reason == "user_message_pending" and not (tmp_path / "old.txt").exists()


async def test_tui_guidance_stays_with_the_selected_running_session(tmp_path):
    from textual.widgets import Input

    from aster.ui.app import AsterTui

    class Model:
        def __init__(self):
            self.started, self.release = asyncio.Event(), asyncio.Event()
            self.requests = []

        async def complete(self, messages, _tools):
            self.requests.append(json.dumps(messages))
            if len(self.requests) == 1:
                self.started.set()
                await self.release.wait()
            return ModelReply(text="Answer")

    app = AsterTui(tmp_path / "a", Settings(memory_enabled=False, completion="off"))
    first_model, second_model = Model(), Model()
    app.runtime.model = first_model
    async with app.run_test() as pilot:
        app._start_task("First objective")
        await first_model.started.wait()
        first, first_worker = app.runtime, app.task_worker
        app._command("/new", str(tmp_path / "b"))
        app.runtime.model = second_model
        app._start_task("Second objective")
        await second_model.started.wait()
        second, second_worker = app.runtime, app.task_worker
        prompt = app.query_one("#prompt", Input)
        prompt.value = "Guidance exclusively for B"
        await pilot.press("enter")
        app._command("/switch", "1")
        prompt.value = "Guidance exclusively for A"
        await pilot.press("enter")
        assert first.state.user_inbox[0]["content"] == "Guidance exclusively for A"
        assert second.state.user_inbox[0]["content"] == "Guidance exclusively for B"
        first_model.release.set()
        second_model.release.set()
        await first_worker.wait()
        await second_worker.wait()
        assert "Guidance exclusively for A" in first_model.requests[-1]
        assert "Guidance exclusively for B" not in first_model.requests[-1]
        assert "Guidance exclusively for B" in second_model.requests[-1]
        assert "Guidance exclusively for A" not in second_model.requests[-1]
