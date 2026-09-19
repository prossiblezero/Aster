"""Protocol and budget regressions; no network or external model is needed."""

import asyncio
import json

import httpx
import pytest

from aster.config import Settings
from aster.context import ContextBudgetError, ContextBuilder, ContextHistoryError, history_groups
from aster.provider import ModelClient, ProviderError, request_payload
from aster.providers.codec import anthropic_reply, chat_reply
from aster.schema import RunState, TaskSpec
from aster.tools import CORE_TOOLS, definitions


def exchange(index, text="observed"):
    return [
        {
            "type": "reasoning",
            "id": f"r{index}",
            "summary": [],
            "encrypted_content": f"opaque{index}",
        },
        {
            "type": "function_call",
            "call_id": f"c{index}",
            "name": "read_file",
            "arguments": json.dumps({"path": f"file{index}.py"}),
        },
        {"type": "function_call_output", "call_id": f"c{index}", "output": text},
    ]


@pytest.mark.parametrize("protocol", ["responses", "chat", "anthropic"])
def test_budget_counts_actual_tool_schemas_and_protocol_payload(protocol):
    state = RunState(task=TaskSpec(prompt="Find and fix the bug without losing evidence."))
    state.messages = [{"role": "tool", "content": "return invalid_value\n" * 80} for _ in range(50)]
    state.messages.append({"role": "tool", "content": "test_bug passed", "important": True})
    builder = ContextBuilder(4096, 512)
    tools = definitions(sorted(CORE_TOOLS))
    prompt, report = builder.build(
        state, state.task.prompt, tools=tools, protocol=protocol, model="test-model"
    )
    payload = request_payload(
        prompt, tools, protocol=protocol, model="test-model", output_tokens=512
    )
    assert report.estimated_tokens == builder.count(payload) <= report.budget
    assert report.compacted and report.removed_messages > 0
    assert state.task.prompt in str(prompt) and "test_bug passed" in str(prompt)


def test_compaction_retains_full_call_batches_and_reasoning_beyond_old_slice_limit():
    state = RunState(task=TaskSpec(prompt="Keep investigating."))
    history = [item for i in range(24) for item in exchange(i, "long observation " * 300)]
    builder = ContextBuilder(2100, 512)
    prompt, report = builder.build(state, state.task.prompt, structured_history=history)
    assert report.compacted and report.estimated_tokens <= report.budget
    groups = history_groups(history)
    assert all(len(group) == 3 for group in groups)
    assert history[-3]["encrypted_content"] == "opaque23"
    assert history[-1]["call_id"] == "c23"
    assert prompt[2:-1] == history


def test_parallel_batch_retains_all_results():
    first, second = exchange(1), exchange(2)
    history = first[:2] + second[1:2] + first[2:] + second[2:]
    assert history_groups(history) == [history]
    with pytest.raises(ContextHistoryError, match="missing_tool_output"):
        history_groups(history[:-1])
    with pytest.raises(ContextHistoryError, match="orphan_tool_output"):
        history_groups(history[2:])


def test_budget_failure_never_truncates_current_task_or_mutates_input():
    state = RunState(task=TaskSpec(prompt="任务非常长" * 3000))
    history = exchange(1)
    original = json.dumps(history)
    with pytest.raises(ContextBudgetError, match="mandatory request"):
        ContextBuilder(1024, 256).build(state, state.task.prompt, structured_history=history)
    assert json.dumps(history) == original
    assert len(state.task.prompt) == 15000


def test_feedback_not_duplicated_and_tokenizer_control_strings_are_plain_data():
    feedback = "Please fix <|endoftext|> in the file."
    state = RunState(task=TaskSpec(prompt="Fix file."))
    state.messages = [
        {"role": "user", "content": feedback},
        {"role": "assistant", "content": "Inspecting."},
    ]
    history = [
        {"role": "assistant", "content": "Inspecting."},
        {"role": "user", "content": feedback},
    ]
    prompt, _ = ContextBuilder(4096, 512).build(
        state, state.task.prompt, structured_history=history
    )
    assert str(prompt).count(feedback) == 1
    assert str(prompt).count("Inspecting.") == 1


def test_chat_native_calls_and_reasoning_survive_round_trip():
    raw = {
        "role": "assistant",
        "content": None,
        "reasoning_content": "opaque reasoning",
        "tool_calls": [
            {
                "id": "c1",
                "type": "function",
                "function": {"name": "read_file", "arguments": '{"path":"a.py"}'},
            }
        ],
    }
    reply = chat_reply({"choices": [{"message": raw}]})
    history = reply.raw + [
        {"type": "function_call_output", "call_id": "c1", "output": "file contents"}
    ]
    assert history_groups(history) == [history]
    payload = request_payload(history, definitions(["read_file"]), protocol="chat")
    assert payload["tools"][0]["function"]["name"] == "read_file"
    assert payload["messages"][0] == raw
    assert payload["messages"][1] == {
        "role": "tool",
        "tool_call_id": "c1",
        "content": "file contents",
    }


def test_anthropic_native_thinking_tool_results_and_system_are_preserved():
    blocks = [
        {"type": "thinking", "thinking": "opaque", "signature": "signature"},
        {"type": "tool_use", "id": "c1", "name": "read_file", "input": {"path": "a.py"}},
        {"type": "tool_use", "id": "c2", "name": "read_file", "input": {"path": "b.py"}},
    ]
    reply = anthropic_reply({"content": blocks})
    history = reply.raw + [
        {"type": "function_call_output", "call_id": key, "output": "observed"}
        for key in ("c1", "c2")
    ]
    assert history_groups(history) == [history]
    payload = request_payload(
        [{"role": "system", "content": "policy"}] + history,
        definitions(["read_file"]),
        protocol="anthropic",
    )
    assert payload["system"] == "policy"
    assert payload["messages"][0]["content"] == blocks
    assert [x["tool_use_id"] for x in payload["messages"][1]["content"]] == ["c1", "c2"]
    assert payload["tools"][0]["input_schema"]["type"] == "object"


@pytest.mark.parametrize("protocol", ["chat", "anthropic"])
def test_tool_images_are_kept_in_supported_native_shape(protocol):
    history = exchange(1)
    history[-1]["output"] = [
        {"type": "input_text", "text": "diagram"},
        {"type": "input_image", "image_url": "data:image/png;base64,YQ=="},
    ]
    payload = request_payload(history, [], protocol=protocol)
    assert "YQ==" in str(payload)
    if protocol == "chat":
        assert payload["messages"][-1]["content"][0]["type"] == "image_url"
    else:
        assert (
            payload["messages"][-1]["content"][0]["content"][1]["source"]["media_type"]
            == "image/png"
        )


def mock_transport(monkeypatch, handler):
    original = httpx.AsyncClient
    monkeypatch.setattr(
        "aster.providers.client.httpx.AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs),
    )
    sleeps = []

    async def no_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr("aster.providers.client.asyncio.sleep", no_sleep)
    return sleeps


def test_transient_provider_retry_is_bounded_and_preserves_usage(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            raise httpx.ConnectError("temporary", request=request)
        if len(calls) == 2:
            return httpx.Response(
                429, json={"error": {"message": "rate limited"}}, headers={"Retry-After": "999"}
            )
        return httpx.Response(
            200, json={"output_text": "done", "usage": {"input_tokens": 10, "output_tokens": 2}}
        )

    sleeps = mock_transport(monkeypatch, handler)
    client = ModelClient(Settings(api_key="test-key-secret"))
    reply = asyncio.run(client.complete([{"role": "user", "content": "hi"}], []))
    assert reply.text == "done" and reply.usage["total_tokens"] == 12
    assert len(calls) == 3 and sleeps == [0.25, 2]
    assert all(request.content == calls[0].content for request in calls)


def test_gateway_timeout_html_is_retried_before_success(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(524, text="<html>Origin response timed out</html>")
        return httpx.Response(
            200, json={"output_text": "done", "usage": {"input_tokens": 10, "output_tokens": 2}}
        )

    sleeps = mock_transport(monkeypatch, handler)
    reply = asyncio.run(ModelClient(Settings(api_key="test-key-secret")).complete([], []))
    assert reply.text == "done" and reply.usage["total_tokens"] == 12
    assert len(calls) == 2 and sleeps == [0.25]
    assert calls[0].content == calls[1].content


def test_persistent_gateway_timeout_is_bounded_and_does_not_expose_html(monkeypatch):
    calls = []
    secret = "test-key-secret"

    def handler(request):
        calls.append(request)
        return httpx.Response(
            524, text=f"<html>{secret} https://private-gateway.example/ diagnostic</html>"
        )

    sleeps = mock_transport(monkeypatch, handler)
    with pytest.raises(ProviderError) as caught:
        asyncio.run(ModelClient(Settings(api_key=secret)).complete([], []))
    assert caught.value.details["category"] == "transient_http"
    assert caught.value.details["status"] == 524
    assert caught.value.details["attempts"] == 3
    assert len(calls) == 3 and sleeps == [0.25, 0.5]
    assert all(request.content == calls[0].content for request in calls)
    assert secret not in str(caught.value) and "private-gateway" not in str(caught.value)


def test_invalid_request_is_not_retried_and_error_details_are_redacted(monkeypatch):
    calls = []
    secret = "test-key-secret"

    def handler(request):
        calls.append(request)
        return httpx.Response(
            400,
            json={
                "error": {
                    "code": "invalid_tool_history",
                    "message": f"orphan output {secret} https://gateway.example/?api_key=secret password=hello",
                }
            },
        )

    sleeps = mock_transport(monkeypatch, handler)
    client = ModelClient(Settings(api_key=secret))
    with pytest.raises(ProviderError) as caught:
        asyncio.run(client.complete([], []))
    error = caught.value
    assert len(calls) == 1 and sleeps == []
    assert error.details["status"] == 400 and error.details["code"] == "invalid_tool_history"
    assert "orphan output" in str(error)
    assert (
        secret not in str(error)
        and "hello" not in str(error)
        and "gateway.example" not in str(error)
    )


def test_persistent_transport_error_reports_final_attempt_count(monkeypatch):
    def handler(request):
        raise httpx.ReadTimeout("unsafe URL and details", request=request)

    sleeps = mock_transport(monkeypatch, handler)
    with pytest.raises(ProviderError) as caught:
        asyncio.run(ModelClient(Settings(api_key="test-key-secret")).complete([], []))
    assert caught.value.details["attempts"] == 3
    assert caught.value.details["category"] == "transport"
    assert "unsafe" not in str(caught.value) and len(sleeps) == 2


def test_malformed_tool_arguments_fail_before_any_execution(monkeypatch):
    def handler(request):
        return httpx.Response(
            200,
            json={
                "output": [
                    {
                        "type": "function_call",
                        "name": "write_file",
                        "call_id": "c1",
                        "arguments": "not json",
                    }
                ]
            },
        )

    sleeps = mock_transport(monkeypatch, handler)
    with pytest.raises(ProviderError, match="invalid_response"):
        asyncio.run(ModelClient(Settings(api_key="test-key-secret")).complete([], []))
    assert sleeps == []
