"""Exercise terminal keys and the real local command dispatch while the model waits."""

import asyncio

import pytest

from aster.config import load_settings
from aster.schema import ModelReply
from aster.ui.app import ASTER_MARK, AsterTui


class WaitingModel:
    def __init__(self):
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.requests = 0

    async def complete(self, *_args):
        self.requests += 1
        self.started.set()
        try:
            await asyncio.Event().wait()
        finally:
            self.cancelled.set()
        return ModelReply(text="unreachable")


def log_text(app):
    return "\n".join(line.text for line in app.query_one("#log").lines)


@pytest.mark.parametrize("command", ["quit", "exit", "/quit", "/exit"])
async def test_quit_exits_without_ever_calling_model(tmp_path, command):
    app = AsterTui(tmp_path, load_settings(interactive=True, completion="off"))
    app.runtime.model = model = WaitingModel()
    async with app.run_test() as pilot:
        app.query_one("#prompt").value = command
        await pilot.press("enter")
        await pilot.pause()
        assert not app.is_running
    assert model.requests == 0 and app.runtime.state is None


@pytest.mark.parametrize("action", ["quit", "/cancel", "ctrl+c", "ctrl+q", "escape"])
async def test_busy_input_help_and_cancel_remain_responsive(tmp_path, action):
    app = AsterTui(tmp_path, load_settings(interactive=True, completion="off"))
    app.runtime.model = model = WaitingModel()
    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt")
        prompt.value = "等待模型"
        await pilot.press("enter")
        await asyncio.wait_for(model.started.wait(), 2)
        assert not prompt.disabled and app.focused is prompt
        assert "等待模型" in str(app.query_one("#progress").content)
        prompt.value = "/status"
        await pilot.press("enter")
        assert "运行中" in log_text(app)
        prompt.value = "下一条任务"
        await pilot.press("enter")
        assert prompt.value == "下一条任务" and model.requests == 1
        if action in {"quit", "/cancel"}:
            prompt.value = action
            await pilot.press("enter")
        else:
            await pilot.press(action)
        await asyncio.wait_for(model.cancelled.wait(), 2)
        await pilot.pause()
        assert app.runtime.state.stop_reason == "cancelled"
        if action in {"quit", "ctrl+q"}:
            assert not app.is_running
        else:
            assert app.is_running and not prompt.disabled


async def test_chinese_welcome_local_commands_completion_and_history(tmp_path):
    app = AsterTui(tmp_path, load_settings(interactive=True))
    app.runtime.model = model = WaitingModel()
    async with app.run_test(size=(100, 40)) as pilot:
        welcome = str(app.query_one("#welcome").content)
        assert ASTER_MARK in welcome and "终端编程伙伴" in welcome
        prompt = app.query_one("#prompt")
        prompt.value = "/he"
        await pilot.pause()
        assert app.query_one("#suggestions").display
        await pilot.press("tab")
        assert prompt.value == "/help "
        await pilot.press("enter")
        assert "/resume" in log_text(app) and "快捷键" in log_text(app)
        for command in (
            "/status",
            "/session",
            "/context",
            "/usage",
            "/history",
            "/tools",
            "/skills",
            "/memory",
            "/working-memory",
            "/agents",
            "/todos",
            "/model",
            "/thinking off",
            "/stream off",
            "/unknown",
        ):
            prompt.value = command
            await pilot.press("enter")
        assert "未知命令" in log_text(app)
        assert "命令未执行" not in log_text(app)
        assert not app.settings.stream
        assert model.requests == 0 and app.runtime.state is None
        await pilot.press("up")
        assert prompt.value == "/unknown"
        await pilot.press("down")
        assert prompt.value == ""


async def test_plan_mode_clear_and_resume_latest(tmp_path):
    class ReplyModel:
        async def complete(self, *_args):
            return ModelReply(text="计划已经列出")

    settings = load_settings(interactive=True, completion="off")
    app = AsterTui(tmp_path, settings)
    app.runtime.model = ReplyModel()
    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt")
        prompt.value = "/plan 分析项目"
        await pilot.press("enter")
        await app.task_worker.wait()
        state = app.runtime.state
        assert state.mode == "plan" and state.task.prompt == "分析项目"
        prompt.value = "/clear"
        await pilot.press("enter")
        assert app.runtime.state is state and not app.query_one("#log").lines
        prompt.value = "/new"
        await pilot.press("enter")
        assert app.runtime.state is None
        prompt.value = "/resume latest"
        await pilot.press("enter")
        await app.task_worker.wait()
        assert app.runtime.state.session_id == state.session_id
        assert app.runtime.state.mode == "plan"
        prompt.value = "/plan-exit"
        await pilot.press("enter")
        assert app.runtime.state.mode == "work"


@pytest.mark.parametrize("size", [(80, 24), (45, 20), (60, 15)])
async def test_prompt_stays_visible_in_small_terminals(tmp_path, size):
    app = AsterTui(tmp_path, load_settings(interactive=True))
    async with app.run_test(size=size) as pilot:
        await pilot.pause()
        prompt = app.query_one("#prompt")
        assert prompt.region.bottom <= size[1]
        assert prompt.region.width > 0
        prompt.value = "/"
        await pilot.pause()
        assert prompt.region.bottom <= size[1]
