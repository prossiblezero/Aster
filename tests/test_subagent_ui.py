"""Clickable delegation uses real workers, persisted history and cancellation."""
import asyncio
import json

import pytest
from textual.widgets import Input, OptionList

from aster.config import Settings
from aster.schema import ModelReply, ToolCall
from aster.ui.agent_widgets import AgentPanel
from aster.ui.app import AsterTui

pytestmark = pytest.mark.core


async def eventually(predicate):
    async with asyncio.timeout(5):
        while not predicate():
            await asyncio.sleep(0.01)


async def start_child(app, pilot, text):
    app.screen.query_one("#agent-input", Input).value = text
    assert await pilot.click("#agent-start")
    await pilot.pause()


async def test_click_start_detail_followup_and_reopen_without_parent_model_call(tmp_path):
    (tmp_path / "module.py").write_text("answer = 42\n")
    calls = []
    class Investigate:
        async def complete(self, messages, tools):
            calls.append(json.dumps(messages, ensure_ascii=False))
            if len(calls) == 1:
                call = ToolCall(name="read_file", arguments={"path": "module.py"})
                return ModelReply(calls=[call], raw=[{"type": "function_call", "name": call.name,
                                  "call_id": call.call_id, "arguments": json.dumps(call.arguments)}])
            return ModelReply(text="调查结论：module.py 中 answer 为 42。" if len(calls) == 2 else "补充结论：已有读取证据。")

    settings = Settings(completion="off", auto_dream=False, memory_enabled=False)
    app = AsterTui(tmp_path, settings)
    app.runtime.model = Investigate()
    async with app.run_test(size=(45, 20)) as pilot:
        assert app.query_one("#show-help").region.right <= 45
        assert await pilot.click("#show-agents")
        assert isinstance(app.screen, AgentPanel)
        await start_child(app, pilot, "调查 module.py 的常量")
        await eventually(lambda: bool(app.runtime.workers.workers))
        child = next(iter(app.runtime.workers.workers.values()))
        await asyncio.wait_for(asyncio.shield(child.future), 5)
        app.screen.refresh_agents()
        assert "调查结论" in app.screen._detail and "read_file" in app.screen._detail
        assert app.runtime.state.turn == 0 and len(calls) == 2
        assert app.settings.max_workers == 0
        assert child.runtime.settings.read_only
        assert app.screen.query_one("#agent-close").region.right <= 45
        app.screen.query_one(Input).value = "解释这个常量的用途"
        assert await pilot.click("#agent-send")
        await eventually(lambda: len(calls) == 3)
        await asyncio.wait_for(asyncio.shield(child.future), 5)
        assert "调查结论" in calls[-1] and "解释这个常量" in calls[-1]
        run_id = app.runtime.state.run_id
        assert await pilot.click("#agent-close")

    reopened = AsterTui(tmp_path, Settings(completion="off", auto_dream=False, memory_enabled=False))
    reopened.runtime.model = Investigate()
    reopened.runtime.resume(run_id)
    async with reopened.run_test() as pilot:
        reopened._command("/agents", "")
        await pilot.pause()
        assert "补充结论" in reopened.screen._detail
        assert "已完成" in str(reopened.screen.query_one(OptionList).get_option_at_index(0).prompt)
        assert not reopened.runtime.workers.workers  # Inspecting saved work must not restart it.
        reopened.screen.query_one(Input).value = "重开后的追问"
        assert await pilot.click("#agent-send")
        await eventually(lambda: len(calls) == 4)
        restored = next(iter(reopened.runtime.workers.workers.values()))
        await asyncio.wait_for(asyncio.shield(restored.future), 5)
        assert "补充结论" in calls[-1] and "重开后的追问" in calls[-1]


async def test_busy_parent_can_delegate_cancel_and_quit_cleans_child(tmp_path):
    class Wait:
        def __init__(self):
            self.calls = 0
        async def complete(self, *_):
            self.calls += 1
            await asyncio.Event().wait()

    app = AsterTui(tmp_path, Settings(completion="off", auto_dream=False))
    app.runtime.model = model = Wait()
    async with app.run_test(size=(100, 30)) as pilot:
        assert await pilot.click("#show-agents")
        await start_child(app, pilot, "先调查")
        await eventually(lambda: model.calls == 1)
        run_id, manager = app.runtime.state.run_id, app.runtime.workers
        child = next(iter(manager.workers.values()))
        assert await pilot.click("#agent-close")
        app._start_task("主任务")
        await eventually(lambda: model.calls == 2)
        assert app.runtime.state.run_id == run_id and app.runtime.workers is manager
        assert app.runtime.state.task.prompt == "主任务"
        assert await pilot.click("#show-agents")
        await start_child(app, pilot, "并行调查")
        await eventually(lambda: model.calls == 3)
        assert app.settings.max_workers == 0
        app.screen.refresh_agents()
        assert "运行中" in str(app.screen.query_one(OptionList).get_option_at_index(0).prompt)
        assert await pilot.click("#agent-stop")
        await eventually(child.future.done)
        assert child.runtime.state.stop_reason == "worker_cancelled"
        assert app.busy
        children = list(app.runtime.workers.workers.values())
        await pilot.press("ctrl+q")
        await pilot.pause()
        assert not app.is_running
        assert all(child.future.done() for child in children)
        assert app.runtime.state.stop_reason == "cancelled"


async def test_next_parent_turn_waits_for_child_and_wait_is_cancellable(tmp_path):
    class ReplyThenWait:
        def __init__(self):
            self.calls = 0
        async def complete(self, *_):
            self.calls += 1
            if self.calls == 1:
                return ModelReply(text="主任务完成。")
            await asyncio.Event().wait()

    app = AsterTui(tmp_path, Settings(completion="off", auto_dream=False))
    app.runtime.model = model = ReplyThenWait()
    async with app.run_test() as pilot:
        app._start_task("第一轮主任务")
        await app.task_worker.wait()
        previous_run = app.runtime.state.run_id
        assert await pilot.click("#show-agents")
        await start_child(app, pilot, "后续调查")
        await eventually(lambda: model.calls == 2)
        child = next(iter(app.runtime.workers.workers.values()))
        assert await pilot.click("#agent-close")
        app._start_task("第二轮主任务")
        await pilot.pause()
        assert app.busy and model.calls == 2
        assert "等待子任务交付" in "\n".join(text for text, _ in app._view()["log"])
        await pilot.press("ctrl+c")
        await pilot.pause()
        assert child.future.done() and not app.busy
        assert app.runtime.state.run_id == previous_run
