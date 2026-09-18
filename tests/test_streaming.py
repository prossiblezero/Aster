"""Production Responses streams expose progress but authorize only complete replies."""

import asyncio
import json

import httpx
import pytest

from aster.config import Settings
from aster.provider import ModelClient, ProviderError
from aster.runtime import Runtime
from aster.schema import TaskSpec


def event(kind, **fields):
    return ("data: " + json.dumps({"type": kind, **fields}, ensure_ascii=False) + "\n\n").encode()


def completed(*, text="done", output=None, status="completed"):
    return {
        "status": status,
        "output": output
        if output is not None
        else [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text}],
            }
        ],
        "usage": {"input_tokens": 10, "output_tokens": 2},
    }


class Chunks(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks

    async def __aiter__(self):
        for chunk in self.chunks:
            if isinstance(chunk, Exception):
                raise chunk
            yield chunk


def install(monkeypatch, handler):
    original = httpx.AsyncClient
    monkeypatch.setattr(
        "aster.provider.httpx.AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs),
    )


def client(**kwargs):
    return ModelClient(
        Settings(api_key="test-stream-secret", **kwargs).model_copy(update={"stream": True})
    )


async def test_text_arrives_before_terminal_with_split_utf8_and_json(monkeypatch):
    release, received = asyncio.Event(), asyncio.Event()
    events, requests = [], []
    terminal = completed(text="你好")

    class Controlled(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b": heartbeat\r\n\r\n"
            data = event("response.output_text.delta", delta="你好", output_index=0, item_id="m1")
            for start in range(0, len(data), 2):
                yield data[start : start + 2]
            await release.wait()
            yield event("response.completed", response=terminal)

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, stream=Controlled())

    def observe(value):
        events.append(value)
        if value["type"] == "text_delta":
            received.set()

    install(monkeypatch, handler)
    task = asyncio.create_task(client().complete([], [], on_event=observe))
    await asyncio.wait_for(received.wait(), 1)
    assert not task.done() and not any(e["type"] == "completed" for e in events)
    release.set()
    reply = await task
    expected = ModelClient._responses_reply(terminal, ModelClient._usage(terminal["usage"]))
    assert reply.model_dump(exclude={"diagnostics"}) == expected.model_dump(exclude={"diagnostics"})
    assert reply.diagnostics["classification"] == "success"
    assert "".join(e["text"] for e in events if e["type"] == "text_delta") == "你好"
    assert events[-1]["type"] == "completed" and events[-1]["usage"]["total_tokens"] == 12
    assert events[-1]["first_text_seconds"] <= events[-1]["elapsed_seconds"]
    assert requests[0]["stream"] is True and "reasoning" not in requests[0]


async def test_tool_deltas_wait_for_complete_response_and_duplicate_terminal_counts_once(
    monkeypatch,
):
    calls = [
        {
            "type": "function_call",
            "call_id": f"c{i}",
            "name": "read_file",
            "arguments": json.dumps({"path": f"{i}.txt"}),
        }
        for i in range(2)
    ]
    terminal = completed(output=calls)
    chunks = [
        event(
            "response.function_call_arguments.delta", delta='{"path":', output_index=0, item_id="f0"
        ),
        event(
            "response.function_call_arguments.delta", delta='"0.txt"}', output_index=0, item_id="f0"
        ),
        event("response.completed", response=terminal),
        event("response.completed", response=terminal),
    ]
    install(monkeypatch, lambda _: httpx.Response(200, stream=Chunks(chunks)))
    events = []
    reply = await client().complete([], [], on_event=events.append)
    assert [call.call_id for call in reply.calls] == ["c0", "c1"]
    assert reply.usage["usage_requests"] == 1
    assert len([e for e in events if e["type"] == "completed"]) == 1
    assert len([e for e in events if e["type"] == "tool_delta"]) == 2


@pytest.mark.parametrize(
    "tail,category",
    [
        (b"", "incomplete_response"),
        (b"data: [DONE]\n\n", "incomplete_response"),
        (event("error", message="test-stream-secret"), "provider_error"),
        (
            event("response.completed", response={"status": "in_progress", "output": []}),
            "invalid_response",
        ),
        (event("response.completed", response=completed())[:-1], "incomplete_response"),
        (b'data: {"type":[]}\n\n', "invalid_response"),
    ],
)
async def test_partial_tool_never_executes_and_errors_are_safe(
    tmp_path, monkeypatch, tail, category
):
    call = {
        "type": "function_call",
        "name": "write_file",
        "call_id": "c1",
        "arguments": '{"path":"bad","content":"bad"}',
    }
    chunks = [event("response.output_item.done", item=call), tail]
    requests, events = [], []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, stream=Chunks(chunks))

    install(monkeypatch, handler)
    runtime = Runtime(tmp_path, Settings(approval="auto", auto_dream=False), model=client())
    state = await runtime.run(TaskSpec(prompt="Write bad"))
    assert state.stop_reason == "provider_error" and state.step == 0
    assert not (tmp_path / "bad").exists() and len(requests) == 1
    with pytest.raises(ProviderError) as caught:
        await client().complete([], [], on_event=events.append)
    assert caught.value.details["category"] == category
    assert events[-1]["type"] == "error"
    assert "test-stream-secret" not in str(caught.value) + json.dumps(events)


@pytest.mark.parametrize("status", ["failed", "incomplete"])
async def test_unsuccessful_terminal_preserves_usage_without_retry(monkeypatch, status):
    terminal = completed(status=status)
    terminal["incomplete_details"] = {"reason": "max_output_tokens"}
    requests, events = [], []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, stream=Chunks([event("response." + status, response=terminal)]))

    install(monkeypatch, handler)
    with pytest.raises(ProviderError) as caught:
        await client().complete([], [], on_event=events.append)
    assert caught.value.details["usage"]["total_tokens"] == 12
    assert len(requests) == 1 and events[-1]["type"] == "error"


async def test_malformed_final_arguments_reject_whole_batch(tmp_path, monkeypatch):
    calls = [
        {
            "type": "function_call",
            "name": "write_file",
            "call_id": "c1",
            "arguments": '{"path":"bad","content":"bad"}',
        },
        {"type": "function_call", "name": "write_file", "call_id": "c2", "arguments": '{"path":'},
    ]
    install(
        monkeypatch,
        lambda _: httpx.Response(
            200, stream=Chunks([event("response.completed", response=completed(output=calls))])
        ),
    )
    runtime = Runtime(tmp_path, Settings(approval="auto", auto_dream=False), model=client())
    state = await runtime.run(TaskSpec(prompt="Write bad"))
    assert state.step == 0 and state.usage["total_tokens"] == 12
    assert not (tmp_path / "bad").exists()


@pytest.mark.parametrize(
    "arguments,status",
    [(None, "completed"), ("{}", "incomplete"), ("{}", "cancelled"), ("{}", "unknown")],
)
async def test_terminal_requires_complete_tool_arguments(monkeypatch, arguments, status):
    call = {"type": "function_call", "name": "list_files", "call_id": "c1", "status": status}
    if arguments is not None:
        call["arguments"] = arguments
    install(
        monkeypatch,
        lambda _: httpx.Response(
            200, stream=Chunks([event("response.completed", response=completed(output=[call]))])
        ),
    )
    events = []
    with pytest.raises(ProviderError, match="invalid_response"):
        await client().complete([], [], on_event=events.append)
    assert not any(value["type"] == "completed" for value in events)


async def test_retry_attempts_reset_progress_and_remain_bounded(monkeypatch):
    requests, events = [], []

    def handler(request):
        requests.append(request)
        chunks = [
            event("response.output_text.delta", delta="discarded", output_index=0, item_id="m0"),
            httpx.ReadError("test-stream-secret"),
        ]
        if len(requests) == 3:
            chunks = [event("response.completed", response=completed())]
        return httpx.Response(200, stream=Chunks(chunks))

    install(monkeypatch, handler)
    reply = await client().complete([], [], on_event=events.append)
    assert reply.text == "done" and reply.usage["usage_requests"] == 1
    assert [e["attempt"] for e in events if e["type"] == "begin"] == [1, 2, 3]
    assert [e["attempt"] for e in events if e["type"] == "error"] == [1, 2]


@pytest.mark.parametrize("status,attempts", [(524, 3), (401, 1)])
async def test_http_failure_is_bounded_and_does_not_expose_body(monkeypatch, status, attempts):
    requests, events = [], []

    def handler(request):
        requests.append(request)
        return httpx.Response(status, content=b"test-stream-secret https://private.example/api")

    install(monkeypatch, handler)
    with pytest.raises(ProviderError) as caught:
        await client().complete([], [], on_event=events.append)
    assert len(requests) == caught.value.details["attempts"] == attempts
    assert events[-1]["retrying"] is False
    assert "test-stream-secret" not in str(caught.value) + json.dumps(events)
    assert "private.example" not in str(caught.value) + json.dumps(events)


async def test_cancel_stream_closes_transport_without_retry(monkeypatch):
    started, closed = asyncio.Event(), asyncio.Event()
    requests, events = [], []

    class Pending(httpx.AsyncByteStream):
        async def __aiter__(self):
            started.set()
            await asyncio.Event().wait()
            yield b""

        async def aclose(self):
            closed.set()

    def handler(request):
        requests.append(request)
        return httpx.Response(200, stream=Pending())

    install(monkeypatch, handler)
    task = asyncio.create_task(client().complete([], [], on_event=events.append))
    await asyncio.wait_for(started.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(requests) == 1 and closed.is_set()
    assert events[-1]["category"] == "cancelled"


@pytest.mark.parametrize(
    "parts",
    [
        ("safe test-st", "ream-secret safe"),
        ("safe sk", "-abcdefghijklm", "nopqrstuvwxyz safe"),
        ("safe bea", "rer abcdefghijklm", "nopqrstuvwxyz safe"),
    ],
)
async def test_credentials_are_redacted_across_delta_boundaries(monkeypatch, parts):
    chunks = [
        event("response.output_text.delta", delta=text, output_index=0, item_id="m1")
        for text in parts
    ]
    chunks.append(event("response.completed", response=completed()))
    install(monkeypatch, lambda _: httpx.Response(200, stream=Chunks(chunks)))
    events = []
    await client().complete([], [], on_event=events.append)
    text = "".join(e.get("text", "") for e in events)
    assert text == "safe [REDACTED] safe"


async def test_multiline_sse_and_observer_failure_do_not_change_reply(monkeypatch, caplog):
    terminal = json.dumps(completed())
    wire = (
        'event: response.completed\r\ndata: {"response":\r\ndata: ' + terminal + "}\r\n\r\n"
    ).encode()
    install(monkeypatch, lambda _: httpx.Response(200, stream=Chunks([wire])))

    def failing_observer(_):
        raise ValueError("test-stream-secret")

    reply = await client().complete([], [], on_event=failing_observer)
    assert reply.text == "done" and "test-stream-secret" not in caplog.text


async def test_unterminated_sse_line_and_progress_have_capacity_limits(monkeypatch):
    from aster import streaming

    with monkeypatch.context() as limited:
        limited.setattr(streaming, "MAX_EVENT_BYTES", 32)
        install(limited, lambda _: httpx.Response(200, stream=Chunks([b"data: " + b"x" * 33])))
        with pytest.raises(ProviderError, match="capacity"):
            await client().complete([], [])
    monkeypatch.setattr(streaming, "MAX_PROGRESS_CHARS", 4)
    events = []
    chunks = [
        event("response.output_text.delta", delta="12345678", output_index=0, item_id="m1"),
        event("response.completed", response=completed(text="12345678")),
    ]
    install(monkeypatch, lambda _: httpx.Response(200, stream=Chunks(chunks)))
    reply = await client().complete([], [], on_event=events.append)
    assert reply.text == "12345678"
    assert "".join(e.get("text", "") for e in events) == "1234"
    assert events[-1]["progress_truncated"] is True


async def test_auth_none_stream_sends_no_authorization(monkeypatch):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200, stream=Chunks([event("response.completed", response=completed())])
        )

    install(monkeypatch, handler)
    await client(auth="none").complete([], [])
    assert "authorization" not in requests[0].headers


async def test_old_profile_credentials_cannot_be_reconstructed_from_stream_events(monkeypatch):
    chunks = [
        event("response.output_text.delta", delta=text, output_index=0, item_id="m1")
        for text in ("safe old-prof", "ile-secret safe")
    ]
    chunks.append(event("response.completed", response=completed()))
    install(monkeypatch, lambda _: httpx.Response(200, stream=Chunks(chunks)))
    model, events = client(), []
    model.redaction_secrets = ["old-profile-secret"]
    await model.complete([], [], on_event=events.append)
    assert "".join(e.get("text", "") for e in events) == "safe [REDACTED] safe"


async def test_single_character_deltas_have_a_bounded_event_count(monkeypatch):
    from aster import streaming

    monkeypatch.setattr(streaming, "MAX_PROGRESS_EVENTS", 3, raising=False)
    chunks = [
        event("response.output_text.delta", delta="x", output_index=0, item_id="m1")
        for _ in range(100)
    ]
    chunks.append(event("response.completed", response=completed(text="x" * 100)))
    install(monkeypatch, lambda _: httpx.Response(200, stream=Chunks(chunks)))
    events = []
    reply = await client().complete([], [], on_event=events.append)
    assert reply.text == "x" * 100
    assert len([e for e in events if e["type"] == "text_delta"]) == 3
    assert events[-1]["progress_truncated"] is True


@pytest.mark.parametrize("protocol", ["chat", "anthropic"])
async def test_streaming_other_protocols_fails_explicitly_without_request(monkeypatch, protocol):
    def handler(_):
        raise AssertionError("unsupported stream must fail before network")

    install(monkeypatch, handler)
    with pytest.raises(ProviderError, match="configuration"):
        await client(protocol=protocol).complete([], [])
