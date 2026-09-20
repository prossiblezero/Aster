"""Mouse paths use real widgets and terminal cell coordinates, without slash commands."""
import asyncio
import json

import pytest
from textual.widgets import Input, OptionList, RichLog

from aster.config import Settings
from aster.core.runtime import Runtime
from aster.schema import ModelReply, TaskSpec
from aster.ui.app import AsterTui
from aster.ui.session_widgets import SessionPicker
from aster.ui.widgets import HumanPrompt


class Echo:
    async def complete(self, *_):
        return ModelReply(text="这里是对应请求的回答。")


async def submit(app, pilot, prompt):
    app.query_one("#prompt", Input).value = prompt
    app.query_one("#prompt", Input).focus()
    await pilot.press("enter")
    await app.task_worker.wait()
    await pilot.pause()
    return app.runtime.session_tree.cursor


async def click_branch(app, pilot, entry, summary=False):
    log = app.query_one("#log", RichLog)
    for y, line in enumerate(log.lines):
        x = 0
        for segment in line:
            action = segment.style.meta.get("@click") if segment.style else None
            if action == ("app.branch_at", (entry, summary)):
                log.scroll_to(y=y, animate=False)
                await pilot.pause()
                offset = (log.content_region.x - log.region.x + x + 1,
                          log.content_region.y - log.region.y + y - int(log.scroll_offset.y))
                assert await pilot.click("#log", offset=offset)
                await pilot.pause()
                return
            x += segment.cell_length
    pytest.fail(f"Missing clickable branch point: {entry}")


async def select_filtered(app, pilot, query):
    app.screen.query_one("#session-query", Input).value = query
    await pilot.pause()
    assert app.screen.query_one(OptionList).option_count == 1
    assert await pilot.click("#picker-select")
    await pilot.pause()


@pytest.mark.parametrize("summary", [False, True])
async def test_click_older_dialogue_creates_exact_branch_and_survives_reopen(tmp_path, summary):
    settings = Settings(completion="off", auto_dream=False)
    app = AsterTui(tmp_path, settings)
    app.runtime.model = Echo()
    async with app.run_test(size=(120, 36)) as pilot:
        ancestor = await submit(app, pilot, "共同的祖先请求")
        sibling = await submit(app, pilot, "仅属于旧方案的约束")
        session_id = app.runtime.state.session_id
        await click_branch(app, pilot, ancestor, summary)
        entries = app.runtime.session_tree.entries(session_id)
        branch = [row for row in entries if row["kind"] == "branch"][-1]
        assert branch["payload"]["target_entry"] == ancestor
        history = json.dumps(app.runtime._structured_history, ensure_ascii=False)
        assert ("仅属于旧方案的约束" in history) is summary
        if summary:
            assert "另一分支摘录" in history
        assert any(row["id"] == sibling for row in entries)
        await submit(app, pilot, "新方案的后续请求")
        await pilot.resize_terminal(60, 20)
        await pilot.pause()
        assert not app.query_one("#session-sidebar").display
        log = app.query_one("#log", RichLog)
        assert log.virtual_size.width <= log.scrollable_content_region.width
        await pilot.resize_terminal(120, 36)
        await pilot.pause()
        assert app.query_one("#session-sidebar").display

    reopened = AsterTui(tmp_path, settings)
    async with reopened.run_test(size=(80, 24)) as pilot:
        assert await pilot.click("#show-sessions")
        await select_filtered(reopened, pilot, session_id)
        log = "\n".join(text for text, _ in reopened._view()["log"])
        assert "共同的祖先请求" in log and "新方案的后续请求" in log
        assert "› 你\n仅属于旧方案的约束" not in log
        assert ("仅属于旧方案的约束" in log) is summary
        await click_branch(reopened, pilot, ancestor)
        history = json.dumps(reopened.runtime._structured_history, ensure_ascii=False)
        assert "新方案的后续请求" not in history and "仅属于旧方案的约束" not in history


@pytest.mark.parametrize("size", [(80, 24), (45, 20)])
async def test_click_workspace_adoption_is_explicit_and_keeps_files(tmp_path, size):
    app = AsterTui(tmp_path, Settings(completion="off", auto_dream=False))
    app.runtime.model = Echo()
    async with app.run_test(size=size) as pilot:
        ancestor = await submit(app, pilot, "第一条请求")
        current = await submit(app, pilot, "第二条请求")
        target = tmp_path / "user.txt"
        target.write_text("用户的新文件")
        await click_branch(app, pilot, ancestor)
        assert isinstance(app.screen, HumanPrompt)
        assert app.runtime.session_tree.cursor == current
        assert await pilot.click("#decline")
        await pilot.pause()
        assert app.runtime.session_tree.cursor == current
        await click_branch(app, pilot, ancestor)
        assert await pilot.click("#accept")
        await pilot.pause()
        assert app.runtime.state.stop_reason == "branch_selected"
        assert "第二条请求" not in json.dumps(app.runtime._structured_history, ensure_ascii=False)
        assert target.read_text() == "用户的新文件"


async def test_mouse_session_switch_and_live_completion_failure_cancel_status(tmp_path):
    release, entered = asyncio.Event(), asyncio.Event()
    class Wait:
        async def complete(self, *_):
            entered.set()
            await release.wait()
            return ModelReply(text="后台完成的回答")

    class Fail:
        async def complete(self, *_):
            raise RuntimeError("离线测试模型失败")

    app = AsterTui(tmp_path / "a", Settings(completion="off", auto_dream=False))
    app.runtime.model = Wait()
    async with app.run_test(size=(120, 36)) as pilot:
        app._start_task("后台任务")
        await asyncio.wait_for(entered.wait(), 2)
        first, worker = app.runtime, app.task_worker
        assert await pilot.click("#new-session")
        app.screen.query_one("#answer", Input).value = str(tmp_path / "b")
        assert await pilot.click("#accept")
        await pilot.pause()
        assert app.runtime is not first and worker.is_running
        assert app.query_one("#live-sessions", OptionList).highlighted == 1
        app.runtime.model = Fail()
        await submit(app, pilot, "失败任务")
        assert await pilot.click("#show-sessions")
        await pilot.pause()
        assert isinstance(app.screen, SessionPicker)
        choices = app.screen.query_one(OptionList)
        assert "工作中" in str(choices.get_option("live:0").prompt)
        assert "失败" in str(choices.get_option("live:1").prompt)
        choices.highlighted = 1
        release.set()
        await worker.wait()
        await pilot.pause(0.6)
        assert "已完成" in str(choices.get_option("live:0").prompt)
        assert choices.highlighted == 1  # Polling never moves the user's selection.
        assert await pilot.click("#picker-close")
        await pilot.pause()
        assert await pilot.click("#live-sessions", offset=(2, 1))
        await pilot.pause()
        assert app.runtime is first
        assert "后台完成的回答" in "\n".join(text for text, _ in app._view()["log"])
        release.clear()
        entered.clear()
        app._start_task("可取消任务")
        await asyncio.wait_for(entered.wait(), 2)
        await pilot.pause(0.3)
        assert await pilot.click("#cancel-session")
        await pilot.pause()
        assert app.runtime.state.stop_reason == "cancelled"
        assert "已取消" in str(app.query_one("#live-sessions").get_option("live:0").prompt)
        assert app._views[1]["runtime"].state.phase == "failed"


@pytest.mark.parametrize("size", [(45, 20), (60, 15)])
async def test_history_buttons_and_unloaded_running_session_are_not_live(tmp_path, size):
    settings = Settings(completion="off", auto_dream=False)
    saved = Runtime(tmp_path, settings)
    saved.start(TaskSpec(prompt="中断前的任务"))
    app = AsterTui(tmp_path, settings)
    async with app.run_test(size=size) as pilot:
        assert await pilot.click("#show-sessions")
        await pilot.pause()
        label = app.screen.query_one(OptionList).get_option(saved.state.session_id).prompt
        assert "待恢复" in str(label) and "工作中" not in str(label)
        await select_filtered(app, pilot, saved.state.session_id)
        assert await pilot.click("#show-tree")
        await pilot.pause()
        assert isinstance(app.screen, SessionPicker)
        assert app.screen.query_one("#picker-summary").region.right <= size[0]
        assert app.screen.query_one("#session-choices").content_region.height >= 1
        assert await pilot.click("#picker-close")
        await pilot.pause()
        assert app.query_one("#prompt").region.bottom <= size[1]


async def test_waiting_state_belongs_to_originating_background_session(tmp_path):
    entered, ask = asyncio.Event(), asyncio.Event()
    app = AsterTui(tmp_path / "a", Settings(completion="off", auto_dream=False))
    first = app.runtime
    class Ask:
        async def complete(self, *_):
            entered.set()
            await ask.wait()
            answer = await app._session_question(first, "请选择方案")
            return ModelReply(text=answer or "已取消")
    first.model = Ask()
    async with app.run_test(size=(120, 36)) as pilot:
        app._start_task("需要用户回答的任务")
        await asyncio.wait_for(entered.wait(), 2)
        worker = app.task_worker
        app._open_session(tmp_path / "b")
        ask.set()
        await pilot.pause(0.3)
        assert isinstance(app.screen, HumanPrompt)
        choices = app._main_query("#live-sessions", OptionList)
        assert "等待回答" in str(choices.get_option("live:0").prompt)
        assert "就绪" in str(choices.get_option("live:1").prompt)
        app.screen.query_one("#answer", Input).value = "选择第二种方案"
        assert await pilot.click("#accept")
        await worker.wait()
        await pilot.pause(0.3)
        assert "已完成" in str(choices.get_option("live:0").prompt)
        assert not app._views[0]["waiting"]
