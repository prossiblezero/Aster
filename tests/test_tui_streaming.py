"""A controlled model stream renders before completion and separates retry previews."""

import asyncio

import pytest

from aster.config import Settings
from aster.provider import ModelClient
from aster.schema import ModelReply
from aster.tui import AsterTui


class PausedStream(ModelClient):
    def __init__(self, settings, *, retry=False):
        super().__init__(settings)
        self.partial = asyncio.Event()
        self.release = asyncio.Event()
        self.retry = retry

    async def complete(self, messages, tools, *, on_event=None):
        on_event({"type": "begin", "attempt": 1, "elapsed_seconds": 0})
        if self.retry:
            on_event(
                {
                    "type": "text_delta",
                    "text": "abandoned response",
                    "attempt": 1,
                    "elapsed_seconds": 0.1,
                }
            )
            on_event({"type": "error", "attempt": 1, "elapsed_seconds": 0.2})
            on_event({"type": "begin", "attempt": 2, "elapsed_seconds": 0.3})
        on_event(
            {
                "type": "text_delta",
                "text": "Visible before completion",
                "attempt": 2 if self.retry else 1,
                "elapsed_seconds": 0.4,
            }
        )
        self.partial.set()
        await self.release.wait()
        on_event({"type": "completed", "attempt": 2 if self.retry else 1, "elapsed_seconds": 0.5})
        return ModelReply(text="Visible before completion")


@pytest.mark.parametrize("retry", [False, True])
async def test_tui_displays_incremental_text_before_completion_and_logs_final_once(tmp_path, retry):
    settings = Settings(stream=True, completion="off", auto_dream=False)
    app = AsterTui(tmp_path, settings)
    model = PausedStream(settings, retry=retry)
    app.runtime.model = model
    async with app.run_test() as pilot:
        app.query_one("#prompt").value = "Reply briefly"
        await pilot.press("enter")
        await asyncio.wait_for(model.partial.wait(), 5)
        preview = app.query_one("#model-output")
        assert preview.display
        assert "Visible before completion" in str(preview.content)
        assert "abandoned response" not in str(preview.content)
        assert not app.task_worker.is_finished
        if retry:
            assert any(
                "Previous model attempt incomplete" in line.text
                for line in app.query_one("#log").lines
            )
        model.release.set()
        await app.task_worker.wait()
        await pilot.pause()
        assert not preview.display
        assert app.runtime.state.phase == "completed"
        text = "\n".join(line.text for line in app.query_one("#log").lines)
        assert text.count("Visible before completion") == 1


async def test_tui_cancel_retains_partial_text_as_incomplete_and_runs_no_tools(tmp_path):
    settings = Settings(stream=True, completion="off", auto_dream=False)
    app = AsterTui(tmp_path, settings)
    model = PausedStream(settings)
    app.runtime.model = model
    async with app.run_test() as pilot:
        app.query_one("#prompt").value = "Reply briefly"
        await pilot.press("enter")
        await asyncio.wait_for(model.partial.wait(), 5)
        await app.action_cancel_task()
        await pilot.pause()
        preview = app.query_one("#model-output")
        assert preview.display and "Incomplete model response" in str(preview.content)
        assert "Visible before completion" in str(preview.content)
        assert app.runtime.state.stop_reason == "cancelled"
        text = "\n".join(line.text for line in app.query_one("#log").lines)
        assert f"run_id={app.runtime.state.run_id}" in text
        assert "reason=cancelled" in text and "verification=missing" in text
        assert "/continue" in text and "/resume" in text
        assert not any(
            row["event"] == "tool_result"
            for row in app.runtime.evidence.events(app.runtime.state.run_id)
        )
        assert not app.query_one("#prompt").disabled
