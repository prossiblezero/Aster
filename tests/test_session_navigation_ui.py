import asyncio
import json

from textual.widgets import Input, OptionList

from aster.config import Settings
from aster.schema import ModelReply
from aster.ui.app import AsterTui
from aster.ui.sessions import SessionPicker


class Echo:
    async def complete(self, messages, _):
        return ModelReply(text="对应当前分支的回答")


async def test_tree_picker_restores_search_result_without_copying_ids(tmp_path):
    app = AsterTui(tmp_path, Settings(completion="off"))
    app.runtime.model = Echo()
    async with app.run_test() as pilot:
        await app._run_task("共同约束")
        branch = app.runtime.session_tree.cursor
        await app._run_task("另一个方案")
        app._command("/tree", "共同约束")
        await pilot.pause()
        assert isinstance(app.screen, SessionPicker)
        choices = app.screen.query_one(OptionList)
        assert choices.option_count >= 1
        rows = [choices.get_option_at_index(i).id for i in range(choices.option_count)]
        choices.highlighted = rows.index(branch)
        await pilot.press("enter")
        await pilot.pause()
        assert app.screen.id == "_default"
        assert "另一个方案" not in json.dumps(app.runtime._structured_history, ensure_ascii=False)


async def test_background_runs_and_cancellation_stay_with_their_session(tmp_path):
    entered_a, entered_b, finish_a = asyncio.Event(), asyncio.Event(), asyncio.Event()
    class Wait:
        def __init__(self, entered, finish=None):
            self.entered, self.finish = entered, finish
        async def complete(self, *_):
            self.entered.set()
            await (self.finish or asyncio.Event()).wait()
            return ModelReply(text="A 专属回答")
    app = AsterTui(tmp_path / "a", Settings(completion="off"))
    app.runtime.model = Wait(entered_a, finish_a)
    async with app.run_test() as pilot:
        app._start_task("A 请求")
        await asyncio.wait_for(entered_a.wait(), 2)
        first, first_worker = app.runtime, app.task_worker
        app._command("/new", str(tmp_path / "b"))
        second = app.runtime
        second.model = Wait(entered_b)
        app._start_task("B 请求")
        await asyncio.wait_for(entered_b.wait(), 2)
        assert first._operation_task and second._operation_task
        app._command("/sessions", "")
        await pilot.pause()
        finish_a.set()
        await first_worker.wait()
        # Background replies must be safe while a modal picker is open.
        await pilot.press("escape")
        assert not any("A 专属回答" in text for text, _ in app._view()["log"])
        await app.action_cancel_task()
        assert second.state.stop_reason == "cancelled" and first.state.phase == "completed"
        app._command("/switch", "1")
        assert app.runtime is first
        assert any("A 专属回答" in text for text, _ in app._view()["log"])


async def test_new_session_preserves_per_session_draft_and_local_help(tmp_path):
    app = AsterTui(tmp_path, Settings(completion="off"))
    async with app.run_test():
        prompt = app.query_one("#prompt", Input)
        prompt.value = "尚未发送的想法"
        app._command("/new", "")
        app._command("/switch", "1")
        assert app.query_one("#prompt", Input).value == "尚未发送的想法"
        app._command("/help", "")
        text = "\n".join(row[0] for row in app._view()["log"])
        assert "/tree" in text and "/sessions" in text


async def test_stream_preview_returns_with_its_background_session(tmp_path):
    app = AsterTui(tmp_path / "a", Settings(completion="off"))
    async with app.run_test():
        first = app.runtime
        app._command("/new", str(tmp_path / "b"))
        for event in [{"type": "begin"}, {"type": "text_delta", "text": "A后台片段"},
                      {"type": "reasoning_delta", "text": "A推理摘要"}]:
            app._route_session_event(first, {"event": "model_stream", **event})
        assert "A后台片段" not in app._stream_text
        app._command("/switch", "1")
        assert app._stream_text == "A后台片段" and app._reasoning_text == "A推理摘要"
