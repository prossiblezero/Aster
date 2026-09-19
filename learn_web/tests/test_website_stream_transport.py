"""Contracts for the opt-in website experiment transport, without real model calls."""

import asyncio
import json

import httpx
import pytest
from website_stream_transport import ResponsesStreamingClient

from aster.config import Settings
from aster.provider import ModelClient, ProviderError
from aster.providers import codec
from aster.runtime import Runtime
from aster.schema import TaskSpec


class Chunks(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks

    async def __aiter__(self):
        for chunk in self.chunks:
            if isinstance(chunk, Exception):
                raise chunk
            yield chunk


def mock_stream(monkeypatch, data, status=200):
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(status, stream=Chunks(data))

    original = httpx.AsyncClient
    monkeypatch.setattr(
        "website_stream_transport.httpx.AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs),
    )
    return requests


def event(kind, response):
    return ("data: " + json.dumps({"type": kind, "response": response}) + "\n\n").encode()


def client(tmp_path):
    return ResponsesStreamingClient(Settings(api_key="synthetic-test-secret"), tmp_path / "transport.jsonl")


async def test_multiline_sse_waits_for_complete_tool_and_reuses_parser(tmp_path, monkeypatch):
    terminal = {
        "status": "completed",
        "output": [{"type": "function_call", "call_id": "c1", "name": "write_file", "arguments": '{"path":"x","content":"done"}'}],
        "usage": {"input_tokens": 10, "output_tokens": 2},
    }
    payload = json.dumps(terminal)
    chunks = [
        b": heartbeat\r\n\r\n\r\nevent: response.output_item.added\r\n",
        b'data: {"type":"response.output_item.added",\r\ndata: "item":{"type":"function_call","arguments":"{"}}\r\n\r\n',
        b'event: response.completed\r\ndata: {"type":"response.completed",\r\ndata: "response": ',
        payload[:30].encode(), payload[30:].encode() + b"}\r\n\r\n",
    ]
    requests = mock_stream(monkeypatch, chunks)
    seen = []
    parser = codec.responses_reply

    def parse(data, usage=None):
        seen.append(data)
        return parser(data, usage)

    monkeypatch.setattr(codec, "responses_reply", parse)
    result = await client(tmp_path).complete([], [])
    assert len(result.calls) == 1 and result.calls[0].arguments == {"path": "x", "content": "done"}
    assert result.usage["total_tokens"] == 12 and seen == [terminal]
    assert len(requests) == 1 and requests[0]["stream"] is True
    assert requests[0]["reasoning"] == {"effort": "low"}
    audit = [json.loads(row) for row in (tmp_path / "transport.jsonl").read_text().splitlines()]
    assert audit[-1]["event_count"] == 2 and audit[-1]["terminal"] == "completed"
    assert audit[-1]["first_event_seconds"] is not None
    assert "write_file" not in (tmp_path / "transport.jsonl").read_text()


@pytest.mark.parametrize("status", ["incomplete", "failed"])
async def test_terminal_failures_use_existing_completion_check(tmp_path, monkeypatch, status):
    data = {"status": status, "output": [], "usage": {"output_tokens": 7}}
    if status == "incomplete":
        data["incomplete_details"] = {"reason": "max_output_tokens"}
    mock_stream(monkeypatch, [event("response." + status, data)])
    with pytest.raises(ProviderError) as caught:
        await client(tmp_path).complete([], [])
    assert caught.value.details["category"] == "incomplete_response"
    assert caught.value.details["usage"]["output_tokens"] == 7


@pytest.mark.parametrize("tail", [b"", b"data: [DONE]\n\n", b'data: {"type":"response.completed","response":{"status":"completed","output":[]}}'])
async def test_eof_without_complete_event_never_returns_tools(tmp_path, monkeypatch, tail):
    mock_stream(monkeypatch, [b'data: {"type":"response.output_item.added","item":{"type":"function_call"}}\n\n', tail])
    with pytest.raises(ProviderError) as caught:
        await client(tmp_path).complete([], [])
    assert caught.value.details["category"] == "incomplete_response"


@pytest.mark.parametrize("status,category", [(524, "transient_http"), (401, "request_rejected")])
async def test_http_error_does_not_log_body_or_secret(tmp_path, monkeypatch, capsys, status, category):
    requests = mock_stream(monkeypatch, [b"synthetic-test-secret https://private.example/ unsafe-body"], status)
    with pytest.raises(ProviderError) as caught:
        await client(tmp_path).complete([], [])
    assert caught.value.details["category"] == category and caught.value.details["status"] == status
    assert len(requests) == 1
    rendered = str(caught.value) + (tmp_path / "transport.jsonl").read_text() + capsys.readouterr().out
    assert all(value not in rendered for value in ["synthetic-test-secret", "private.example", "unsafe-body"])


@pytest.mark.parametrize("error", [httpx.ReadTimeout("synthetic-test-secret"), httpx.ReadError("synthetic-test-secret")])
async def test_transport_exception_is_safe_and_audited(tmp_path, monkeypatch, error):
    mock_stream(monkeypatch, [error])
    with pytest.raises(ProviderError) as caught:
        await client(tmp_path).complete([], [])
    assert caught.value.details["category"] == "transport"
    assert "synthetic-test-secret" not in str(caught.value)
    audit = [json.loads(row) for row in (tmp_path / "transport.jsonl").read_text().splitlines()]
    assert audit[-1]["error_category"] == "transport"


@pytest.mark.parametrize("terminal", [None, "incomplete", "failed"])
async def test_runtime_never_executes_a_tool_without_completed_response(tmp_path, monkeypatch, terminal):
    call = {
        "type": "function_call", "call_id": "partial-call", "name": "write_file",
        "arguments": '{"path":"must-not-exist.txt","content":"partial"}',
    }
    chunks = [("data: " + json.dumps({"type": "response.output_item.done", "item": call}) + "\n\n").encode()]
    if terminal:
        chunks.append(event("response." + terminal, {"status": terminal, "output": [call]}))
    mock_stream(monkeypatch, chunks)
    model = client(tmp_path)
    runtime = Runtime(tmp_path / "workspace", Settings(approval="auto"), model=model)
    state = await runtime.run(TaskSpec(prompt="Write an answer"))
    assert state.stop_reason == "provider_error" and state.phase == "failed"
    assert state.step == 0 and state.changed_paths == []
    assert not (runtime.workspace.root / "must-not-exist.txt").exists()


async def test_stream_cancellation_is_audited_without_retry(tmp_path, monkeypatch):
    started = asyncio.Event()

    class Pending(httpx.AsyncByteStream):
        async def __aiter__(self):
            started.set()
            await asyncio.Event().wait()
            yield b""

    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, stream=Pending())

    original = httpx.AsyncClient
    monkeypatch.setattr(
        "website_stream_transport.httpx.AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs),
    )
    pending = asyncio.create_task(client(tmp_path).complete([], []))
    await asyncio.wait_for(started.wait(), 1)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert len(requests) == 1
    audit = [json.loads(row) for row in (tmp_path / "transport.jsonl").read_text().splitlines()]
    assert audit[-1]["error_category"] == "cancelled"


async def test_importing_experiment_does_not_change_product_transport(monkeypatch):
    requests = mock_stream(monkeypatch, [b'{"status":"completed","output":[],"output_text":"done"}'])
    result = await ModelClient(Settings(api_key="synthetic-test-secret")).complete([], [])
    assert result.text == "done"
    assert "stream" not in requests[0] and "reasoning" not in requests[0]


async def test_invalid_sse_event_type_is_a_safe_provider_error(tmp_path, monkeypatch):
    mock_stream(monkeypatch, [b'data: {"type":[]}\n\n'])
    with pytest.raises(ProviderError) as caught:
        await client(tmp_path).complete([], [])
    assert caught.value.details["category"] == "invalid_response"


@pytest.mark.parametrize("status", ["failed", "incomplete"])
async def test_terminal_diagnostics_are_bounded_redacted_and_exclude_output(tmp_path, monkeypatch, status):
    secret, base_url = "synthetic-test-secret", "https://private-gateway.example/v1"
    data = {
        "status": status,
        "error": {
            "code": "upstream_capacity",
            "message": f"Try later: {secret} {base_url} api_key=other-secret " + "x" * 1500,
            "api_key": secret,
            "base_url": base_url,
            "output": "private-tool-arguments",
        },
        "incomplete_details": {"reason": "max_output_tokens", "response": "private-response"},
        "output": [{"type": "message", "content": "private-tool-arguments"}],
    }
    mock_stream(monkeypatch, [event("response." + status, data)])
    model = ResponsesStreamingClient(Settings(api_key=secret, base_url=base_url), tmp_path / "transport.jsonl")
    with pytest.raises(ProviderError):
        await model.complete([], [])
    audit_text = (tmp_path / "transport.jsonl").read_text()
    final = json.loads(audit_text.splitlines()[-1])
    assert final["error"]["code"] == "upstream_capacity"
    assert final["error"]["message"].startswith("Try later:")
    assert len(final["error"]["message"]) <= 800
    assert final["incomplete_details"] == {"reason": "max_output_tokens"}
    assert "[REDACTED]" in audit_text
    assert all(value not in audit_text for value in [secret, base_url, "other-secret", "private-tool-arguments", "private-response"])


async def test_standalone_sse_error_uses_bounded_redacted_diagnostics(tmp_path, monkeypatch):
    item = {
        "type": "error", "code": "upstream_failure", "param": "input",
        "message": "synthetic-test-secret https://private.example api_key=other-secret " + "x" * 1500,
        "output": "private-tool-arguments",
    }
    mock_stream(monkeypatch, [("data: " + json.dumps(item) + "\n\n").encode()])
    with pytest.raises(ProviderError) as caught:
        await client(tmp_path).complete([], [])
    assert caught.value.details["category"] == "provider_error"
    audit_text = (tmp_path / "transport.jsonl").read_text()
    final = json.loads(audit_text.splitlines()[-1])
    assert final["error"]["code"] == "upstream_failure"
    assert final["error"]["type"] == "error" and final["error"]["param"] == "input"
    assert len(final["error"]["message"]) <= 800
    assert all(value not in audit_text for value in ["synthetic-test-secret", "private.example", "other-secret", "private-tool-arguments"])
