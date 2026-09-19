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
            assert any("上一次模型响应未完成" in line.text for line in app.query_one("#log").lines)
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
        assert preview.display and "模型响应未完成" in str(preview.content)
        assert "Visible before completion" in str(preview.content)
        assert app.runtime.state.stop_reason == "cancelled"
        text = "\n".join(line.text for line in app.query_one("#log").lines)
        assert f"运行编号：{app.runtime.state.run_id}" in text
        assert "原因：cancelled" in text and "验证 未验证" in text
        assert "/continue" in text and "/resume" in text
        assert not any(
            row["event"] == "tool_result"
            for row in app.runtime.evidence.events(app.runtime.state.run_id)
        )
        assert not app.query_one("#prompt").disabled


async def test_real_sse_adapter_streams_reasoning_and_text_into_tui(tmp_path, monkeypatch):
    import json

    import httpx

    settings = Settings(stream=True, api_key="private-stream-secret", completion="off")
    app = AsterTui(tmp_path, settings)
    received, release, closed = asyncio.Event(), asyncio.Event(), asyncio.Event()

    def event(kind, **fields):
        return ("data: " + json.dumps({"type": kind, **fields}) + "\n\n").encode()

    class WireStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield event(
                "response.reasoning_summary_text.delta",
                delta="先检查入口。private-stream-",
                output_index=0,
                item_id="r1",
                summary_index=0,
            )
            yield event(
                "response.reasoning_summary_text.delta",
                delta="secret",
                output_index=0,
                item_id="r1",
                summary_index=0,
            )
            yield event(
                "response.output_text.delta", delta="实时回答", output_index=1, item_id="m1"
            )
            received.set()
            await release.wait()
            yield event(
                "response.completed",
                response={
                    "status": "completed",
                    "output": [
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": "实时回答"}],
                        }
                    ],
                    "usage": {
                        "input_tokens": 12,
                        "output_tokens": 3,
                        "input_tokens_details": {"cached_tokens": 7},
                    },
                },
            )

        async def aclose(self):
            closed.set()

    def handler(request):
        assert json.loads(request.content)["stream"] is True
        return httpx.Response(200, stream=WireStream())

    original = httpx.AsyncClient
    monkeypatch.setattr(
        "aster.provider.httpx.AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs),
    )
    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt")
        prompt.value = "简短回答"
        await pilot.press("enter")
        await asyncio.wait_for(received.wait(), 3)
        assert app.busy
        assert "实时回答" in str(app.query_one("#model-output").content)
        reasoning = app.query_one("#reasoning")
        assert reasoning.display and "先检查入口" in str(reasoning.content)
        assert "private-stream-" not in str(reasoning.content)
        prompt.value = "/thinking off"
        await pilot.press("enter")
        assert not reasoning.display
        prompt.value = "/thinking on"
        await pilot.press("enter")
        assert reasoning.display
        release.set()
        await app.task_worker.wait()
        await pilot.pause()
        text = "\n".join(line.text for line in app.query_one("#log").lines)
        assert text.count("实时回答") == 1 and text.count("先检查入口") == 1
        assert "private-stream-" not in text
        assert app.runtime.state.usage["total_tokens"] == 15 and closed.is_set()
        prompt.value = "/usage"
        await pilot.press("enter")
        text = "\n".join(line.text for line in app.query_one("#log").lines)
        assert "缓存命中：7" in text
