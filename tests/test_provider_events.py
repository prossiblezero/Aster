"""Interrupted generation and UI observers must not manufacture successful actions."""

import asyncio
import json

import httpx
import pytest

from aster.config import Settings
from aster.evidence import EvidenceStore, Redactor
from aster.provider import ModelClient, ProviderError
from aster.providers.usage import normalize_usage
from aster.runtime import Runtime
from aster.schema import TaskSpec, ToolCall


@pytest.mark.parametrize(
    "protocol, response, code",
    [
        (
            "responses",
            {
                "status": "incomplete",
                "incomplete_details": {"reason": "max_output_tokens"},
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "The answer is"}],
                    }
                ],
            },
            "responses:max_output_tokens",
        ),
        (
            "chat",
            {
                "choices": [
                    {
                        "finish_reason": "length",
                        "message": {"role": "assistant", "content": "The answer is"},
                    }
                ]
            },
            "chat:length",
        ),
        (
            "anthropic",
            {"stop_reason": "max_tokens", "content": [{"type": "text", "text": "The answer is"}]},
            "anthropic:max_tokens",
        ),
    ],
)
async def test_truncated_generation_is_not_finalized_or_retried(
    tmp_path, monkeypatch, protocol, response, code
):
    requests = []
    response["usage"] = {"input_tokens": 10, "output_tokens": 20}
    if protocol == "anthropic":
        response["usage"].update(cache_read_input_tokens=0, cache_creation_input_tokens=0)

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=response)

    original = httpx.AsyncClient
    monkeypatch.setattr(
        "aster.providers.client.httpx.AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs),
    )
    settings = Settings(protocol=protocol, api_key="fake-provider-secret")
    client = ModelClient(settings)
    with pytest.raises(ProviderError) as caught:
        await client.complete([], [])
    assert caught.value.details["category"] == "incomplete_response"
    assert caught.value.details["code"] == code
    assert caught.value.details["usage"]["total_tokens"] == 30
    assert len(requests) == 1

    runtime = Runtime(tmp_path, settings, model=client)
    state = await runtime.run(TaskSpec(prompt="Answer"))
    assert state.phase == "failed" and state.stop_reason == "provider_error" and not state.final
    assert state.usage["total_tokens"] == 30
    event = next(
        row for row in runtime.evidence.events(state.run_id) if row["event"] == "provider_error"
    )
    assert event["usage"]["total_tokens"] == 30
    assert len(requests) == 2


async def test_provider_cancellation_is_not_retried(monkeypatch):
    entered = asyncio.Event()
    requests = []

    async def handler(request):
        requests.append(request)
        entered.set()
        await asyncio.Event().wait()

    original = httpx.AsyncClient
    monkeypatch.setattr(
        "aster.providers.client.httpx.AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs),
    )
    client = ModelClient(Settings(api_key="fake-provider-secret"))
    pending = asyncio.create_task(client.complete([], []))
    await asyncio.wait_for(entered.wait(), 1)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert len(requests) == 1


async def test_listener_failure_cannot_lose_a_completed_edit(tmp_path, caplog):
    runtime = Runtime(tmp_path, Settings(approval="auto"))
    runtime.start(TaskSpec(prompt="Answer"))
    heard = []

    def broken_ui(row):
        if row["event"] == "tool_decision":
            raise RuntimeError("private UI detail must not leak")

    runtime.evidence.listeners.extend([broken_ui, lambda row: heard.append(row["event"])])
    call = ToolCall(name="write_file", arguments={"path": "answer.txt", "content": "written"})
    result = await runtime.step(call)
    checkpoint = json.loads(
        runtime.evidence.path("runs", runtime.state.run_id, "checkpoint.json").read_text()
    )
    events = runtime.evidence.events(runtime.state.run_id)
    assert result.status == "success" and (tmp_path / "answer.txt").read_text() == "written"
    assert checkpoint["state"]["changed_paths"] == ["answer.txt"]
    assert call.call_id in checkpoint["state"]["used_call_ids"]
    assert "tool_decision" in heard and "tool_result" in heard
    assert sum(row["event"] == "tool_result" for row in events) == 1
    assert "Evidence listener failed: RuntimeError" in caplog.text
    assert "private UI detail" not in caplog.text
    runtime.abort()


def test_listeners_cannot_mutate_authoritative_rows_or_other_observers(tmp_path):
    store = EvidenceStore(tmp_path, Redactor())
    observed = []

    def mutate(row):
        row["hash"] = "forged"
        row["payload"]["value"] = "forged"

    store.listeners.extend([mutate, observed.append])
    returned = store.emit("run", "sample", payload={"value": "original"})
    persisted = store.events("run")[0]
    assert returned == observed[0] == persisted
    assert returned["hash"] != "forged" and returned["payload"]["value"] == "original"


@pytest.mark.parametrize(
    "raw", [None, [], {"input_tokens": -1, "output_tokens": True}, {"input_tokens": float("inf")}]
)
def test_malformed_usage_never_crashes_a_response(raw):
    assert normalize_usage(raw) == {"usage_requests": 1}


async def test_redaction_preserves_checkpoint_mapping_types_on_resume(tmp_path):
    secret = "private-provider-credential"
    files = {"secret_parser.py": "VALUE = 1\n", "password_rules.py": "MIN_LENGTH = 12\n"}
    for name, content in files.items():
        (tmp_path / name).write_text(content)
    settings = Settings(approval="auto", api_key=secret)
    runtime = Runtime(tmp_path, settings)
    runtime.start(TaskSpec(prompt="Answer"))
    for name in files:
        result = await runtime.step(ToolCall(name="read_file", arguments={"path": name}))
        assert result.status == "success"
    runtime.state.feedback = f"Connection used {secret}"
    runtime.checkpoint()
    path = runtime.evidence.path("runs", runtime.state.run_id, "checkpoint.json")
    assert secret not in path.read_text()
    restored = Runtime(tmp_path, settings)
    restored.resume(runtime.state.run_id)
    assert restored.state.working_memory == runtime.state.working_memory
    assert restored.state.read_evidence == runtime.state.read_evidence
    assert restored._initial_text == runtime._initial_text == files
    assert restored.state.feedback == "Connection used [REDACTED]"
    assert not restored.workspace.diff(restored._initial_text)
    restored.abort()
    runtime.abort()


def test_log_field_redaction_does_not_match_filename_substrings():
    redactor = Redactor(["private-provider-credential"])
    result = redactor.value(
        {
            "secret_parser.py": {"summary": "ordinary code"},
            "secret": "hidden value",
            "events": [{"content": "private-provider-credential"}],
        }
    )
    assert result["secret_parser.py"] == {"summary": "ordinary code"}
    assert result["secret"] == "[REDACTED]"
    assert result["events"] == [{"content": "[REDACTED]"}]


def opaque_history(kind, value, *, thinking="Check the repository before answering."):
    if kind == "responses":
        return [
            {"type": "reasoning", "id": "reasoning-1", "summary": [], "encrypted_content": value}
        ]
    block = (
        {"type": "thinking", "thinking": thinking, "signature": value}
        if kind == "thinking"
        else {"type": "redacted_thinking", "data": value}
    )
    return [{"role": "assistant", "content": [block]}]


@pytest.mark.parametrize("kind", ["responses", "thinking", "redacted_thinking"])
async def test_checkpoint_resume_preserves_opaque_wire_state_but_redacts_audit_copies(
    tmp_path, monkeypatch, kind
):
    # Base64-like provider state can accidentally match a credential-shaped substring.
    opaque = "prefix/sk-" + "Ab9_-" * 280
    protocol = "responses" if kind == "responses" else "anthropic"
    settings = Settings(
        protocol=protocol,
        api_key="test-provider-credential",
        memory_enabled=False,
        completion="off",
    )
    runtime = Runtime(tmp_path, settings)
    runtime.start(TaskSpec(prompt="Inspect the repository"))
    history = opaque_history(kind, opaque)
    runtime._structured_history = history
    runtime.checkpoint()
    checkpoint = json.loads(
        runtime.evidence.path("runs", runtime.state.run_id, "checkpoint.json").read_text()
    )
    assert checkpoint["structured_history"] == history
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        response = (
            {
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "Reviewed."}],
                    }
                ],
            }
            if protocol == "responses"
            else {"stop_reason": "end_turn", "content": [{"type": "text", "text": "Reviewed."}]}
        )
        return httpx.Response(200, json=response)

    original = httpx.AsyncClient
    monkeypatch.setattr(
        "aster.providers.client.httpx.AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs),
    )
    restored = Runtime(tmp_path, settings)
    restored.resume(runtime.state.run_id)
    assert restored._structured_history == history
    await restored.turn()
    assert len(requests) == 1 and opaque in json.dumps(requests[0])
    built = next(
        row
        for row in restored.evidence.events(restored.state.run_id)
        if row["event"] == "context_built"
    )
    audit_path = restored.evidence.path(*built["request_artifact"].split("/"))
    assert opaque not in audit_path.read_text() and "[REDACTED]" in audit_path.read_text()
    event = restored.evidence.emit(restored.state.run_id, "sample", structured_history=history)
    assert opaque not in json.dumps(event) and "[REDACTED]" in json.dumps(event)


def test_checkpoint_native_exception_does_not_exempt_tool_payloads_or_same_named_metadata(tmp_path):
    opaque = "bearer " + "abc123" * 30
    runtime = Runtime(tmp_path, Settings(memory_enabled=False))
    runtime.start(TaskSpec(prompt="Inspect the repository"))
    spoofed = {
        "type": "reasoning",
        "encrypted_content": opaque,
        "signature": opaque,
        "data": opaque,
        "nested": {"role": "assistant", "content": [{"type": "thinking", "signature": opaque}]},
    }
    runtime.state.messages = [{"role": "tool", "content": opaque, "metadata": spoofed}]
    runtime._structured_history = [
        *opaque_history("responses", opaque),
        {"type": "function_call", "call_id": "read-1", "name": "read_file", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "read-1", "output": json.dumps(spoofed)},
    ]
    runtime.checkpoint()
    checkpoint = json.loads(
        runtime.evidence.path("runs", runtime.state.run_id, "checkpoint.json").read_text()
    )
    assert checkpoint["structured_history"][0]["encrypted_content"] == opaque
    assert opaque not in json.dumps(checkpoint["structured_history"][-1])
    assert opaque not in json.dumps(checkpoint["state"]["messages"])
    assert "[REDACTED]" in json.dumps(checkpoint["state"]["messages"])
    runtime.evidence.save(
        ("ordinary.json",), {"structured_history": opaque_history("responses", opaque)}
    )
    assert opaque not in runtime.evidence.path("ordinary.json").read_text()


@pytest.mark.parametrize("kind", ["responses", "thinking", "redacted_thinking"])
@pytest.mark.parametrize(
    "damaged", ["old-[REDACTED]-state", "prefix-known-provider-credential-suffix"]
)
def test_checkpoint_and_resume_reject_damaged_opaque_state_without_replacing_valid_state(
    tmp_path, kind, damaged
):
    settings = Settings(api_key="known-provider-credential", memory_enabled=False)
    runtime = Runtime(tmp_path, settings)
    runtime.start(TaskSpec(prompt="Inspect the repository"))
    path = runtime.evidence.path("runs", runtime.state.run_id, "checkpoint.json")
    before = path.read_bytes()
    runtime._structured_history = opaque_history(kind, damaged)
    with pytest.raises(ValueError, match="opaque_history_(redacted|contains_credential)") as error:
        runtime.checkpoint()
    assert path.read_bytes() == before and damaged not in str(error.value)

    # Reproduce an old/corrupt checkpoint without passing through the new writer.
    checkpoint = json.loads(before)
    checkpoint["structured_history"] = runtime._structured_history
    path.write_text(json.dumps(checkpoint))
    restored = Runtime(tmp_path, settings)
    with pytest.raises(ValueError, match="opaque_history_(redacted|contains_credential)"):
        restored.resume(runtime.state.run_id)
    assert restored.state is None and restored._structured_history == []


@pytest.mark.parametrize("thinking", ["sk-" + "a" * 40, "[REDACTED] original thought"])
def test_signed_thinking_cannot_be_silently_changed_by_redaction(tmp_path, thinking):
    runtime = Runtime(tmp_path, Settings(protocol="anthropic", memory_enabled=False))
    runtime.start(TaskSpec(prompt="Inspect the repository"))
    path = runtime.evidence.path("runs", runtime.state.run_id, "checkpoint.json")
    before = path.read_bytes()
    runtime._structured_history = opaque_history("thinking", "opaque-signature", thinking=thinking)
    with pytest.raises(ValueError, match="signed_thinking_requires_redaction"):
        runtime.checkpoint()
    assert path.read_bytes() == before
    checkpoint = json.loads(before)
    checkpoint["structured_history"] = runtime._structured_history
    path.write_text(json.dumps(checkpoint))
    restored = Runtime(tmp_path, runtime.settings)
    with pytest.raises(ValueError, match="signed_thinking_requires_redaction"):
        restored.resume(runtime.state.run_id)
    assert restored.state is None


def install_provider(monkeypatch, handler):
    original = httpx.AsyncClient
    monkeypatch.setattr(
        "aster.providers.client.httpx.AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs),
    )


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize(
    "output,category,attempts",
    [
        ([], "empty_response", 2),
        ([{"type": "reasoning", "encrypted_content": "opaque"}], "unsupported_output", 1),
        ([{"type": "future_tool_call"}], "unsupported_output", 1),
        ([{"type": "message", "content": [{"type": "refusal", "refusal": "no"}]}], "refusal", 1),
        (
            [{"type": "message", "content": [{"type": "output_text", "text": "  "}]}],
            "empty_response",
            2,
        ),
        (
            [{"type": "function_call", "call_id": "c", "name": "write_file", "arguments": "[]"}],
            "invalid_response",
            1,
        ),
        (
            [{"type": "function_call", "call_id": "c", "name": "write_file", "arguments": "{"}],
            "invalid_response",
            1,
        ),
    ],
)
async def test_terminal_classification_and_bounded_empty_retry(
    monkeypatch, stream, output, category, attempts
):
    requests, events = [], []
    terminal = {
        "status": "completed",
        "output": output,
        "usage": {"input_tokens": 10, "output_tokens": 2},
    }

    def handler(request):
        requests.append(request)
        if stream:
            return httpx.Response(
                200,
                content="data: "
                + json.dumps({"type": "response.completed", "response": terminal})
                + "\n\n",
            )
        return httpx.Response(200, json=terminal)

    install_provider(monkeypatch, handler)
    client = ModelClient(Settings(auth="none", stream=stream))
    with pytest.raises(ProviderError) as caught:
        await client.complete([], [], on_event=events.append)
    assert caught.value.details["category"] == category
    assert len(requests) == caught.value.details["attempts"] == attempts
    assert caught.value.details["usage"]["total_tokens"] == 12 * attempts
    diagnostics = [row for row in events if row["type"] == "diagnostic"]
    assert len(diagnostics) == attempts
    assert all(row["classification"] == category for row in diagnostics)


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize(
    "sequence",
    [[503, "empty", "ok"], ["empty", 503, "ok"], [503, 503, "empty"], ["empty", "empty", "ok"]],
)
async def test_empty_retry_shares_three_attempt_budget_and_preserves_all_usage(
    monkeypatch, stream, sequence
):
    requests, events = [], []

    def handler(request):
        action = sequence[len(requests)]
        requests.append(request)
        if action == 503:
            return httpx.Response(
                503, json={"error": {"message": "busy"}, "usage": {"output_tokens": 1}}
            )
        terminal = {
            "status": "completed",
            "output": []
            if action == "empty"
            else [{"type": "message", "content": [{"type": "output_text", "text": "done"}]}],
            "usage": {"input_tokens": 10, "output_tokens": 2},
        }
        if stream:
            return httpx.Response(
                200,
                content="data: "
                + json.dumps({"type": "response.completed", "response": terminal})
                + "\n\n",
            )
        return httpx.Response(200, json=terminal)

    async def no_sleep(_):
        return

    install_provider(monkeypatch, handler)
    monkeypatch.setattr("aster.providers.client.asyncio.sleep", no_sleep)
    client = ModelClient(Settings(auth="none", stream=stream))
    if sequence[1] == "empty" and sequence[0] == "empty" or sequence[-1] == "empty":
        with pytest.raises(ProviderError) as caught:
            await client.complete([], [], on_event=events.append)
        usage = caught.value.details["usage"]
    else:
        reply = await client.complete([], [], on_event=events.append)
        assert reply.text == "done"
        usage = reply.usage
    attempted = sequence[: len(requests)]
    assert len(requests) == (2 if sequence[:2] == ["empty", "empty"] else 3)
    assert usage["usage_requests"] == len(requests)
    assert usage["output_tokens"] == sum(1 if action == 503 else 2 for action in attempted)
    assert usage.get("input_tokens_requests") == sum(action != 503 for action in attempted)
    assert [row["attempt"] for row in events if row["type"] == "diagnostic"] == list(
        range(1, len(requests) + 1)
    )


@pytest.mark.parametrize("stream", [False, True])
async def test_provider_diagnostics_match_wire_hash_and_exclude_sensitive_payloads(
    monkeypatch, stream
):
    import hashlib

    secret, thinking = "test-provider-secret", "private chain of thought"
    requests, events = [], []
    terminal = {
        "id": "resp-safe",
        "model": "different-model",
        "status": "completed",
        "output": [
            {"type": "reasoning", "summary": [{"text": thinking}], "encrypted_content": thinking},
            {"type": "message", "content": [{"type": "output_text", "text": "done"}]},
        ],
        "usage": {
            "input_tokens": 10,
            "output_tokens": 2,
            "total_tokens": 99,
            "input_tokens_details": {"cached_tokens": 11, "secret": secret},
            "output_tokens_details": {"reasoning_tokens": 3},
            "secret": secret,
            "attribution": {
                "items": {secret: {"input_tokens": 1, "text": thinking}},
                "request_fields": {"instructions": {"input_tokens": 1}, secret: secret},
            },
        },
    }

    def handler(request):
        requests.append(request)
        headers = {
            "x-request-id": "req-safe",
            "authorization": secret,
            "location": "https://private.example/path",
        }
        if stream:
            return httpx.Response(
                200,
                headers=headers,
                content="data: "
                + json.dumps({"type": "response.completed", "response": terminal})
                + "\n\n",
            )
        return httpx.Response(200, headers=headers, json=terminal)

    install_provider(monkeypatch, handler)
    client = ModelClient(Settings(api_key=secret, stream=stream))
    reply = await client.complete(
        [{"role": "user", "content": "你好 " + secret}], [], on_event=events.append
    )
    diagnostic = reply.diagnostics
    encoded = json.dumps(diagnostic)
    assert secret not in encoded and thinking not in encoded and "private.example" not in encoded
    assert diagnostic["payload_sha256"] == hashlib.sha256(requests[0].content).hexdigest()
    assert diagnostic["request_ids"] == {"x-request-id": "req-safe"}
    assert (
        diagnostic["response_id"] == "resp-safe"
        and diagnostic["response_model"] == "different-model"
    )
    assert set(diagnostic["flags"]) == {
        "response_model_mismatch",
        "total_tokens_disagree",
        "cache_read_input_tokens_exceeds_input_tokens",
        "reasoning_tokens_exceeds_output_tokens",
    }
    assert (
        reply.usage["total_tokens"] == 99
    )  # Contradictions are observed, never silently repaired.
    assert diagnostic["raw_usage"]["attribution"]["items"] == [{"input_tokens": 1}]
    assert any(row["type"] == "diagnostic" for row in events)


@pytest.mark.parametrize(
    "protocol,response,category",
    [
        ("chat", {"choices": [{"message": {"refusal": "no", "content": None}}]}, "refusal"),
        (
            "chat",
            {"choices": [{"message": {"reasoning_content": "private"}}]},
            "unsupported_output",
        ),
        ("chat", {"choices": [{"message": {"content": " "}}]}, "empty_response"),
        ("anthropic", {"stop_reason": "refusal", "content": []}, "refusal"),
        (
            "anthropic",
            {"content": [{"type": "thinking", "thinking": "private"}]},
            "unsupported_output",
        ),
        ("anthropic", {"content": []}, "empty_response"),
    ],
)
async def test_other_protocols_classify_refusal_opaque_and_empty(
    monkeypatch, protocol, response, category
):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=response)

    install_provider(monkeypatch, handler)
    with pytest.raises(ProviderError) as caught:
        await ModelClient(Settings(auth="none", protocol=protocol)).complete([], [])
    assert caught.value.details["category"] == category
    assert len(requests) == (2 if category == "empty_response" else 1)


@pytest.mark.parametrize("stream", [False, True])
async def test_missing_usage_and_cancelled_attempt_are_explicit_unknowns(monkeypatch, stream):
    events, entered = [], asyncio.Event()

    async def handler(_):
        entered.set()
        await asyncio.Event().wait()

    install_provider(monkeypatch, handler)
    client = ModelClient(Settings(auth="none", stream=stream))
    task = asyncio.create_task(client.complete([], [], on_event=events.append))
    await asyncio.wait_for(entered.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    diagnostic = next(row for row in events if row["type"] == "diagnostic")
    assert diagnostic["classification"] == "cancelled"
    assert diagnostic["usage"] == {} and diagnostic["usage_observed"] is False
    assert client.last_usage == {}


@pytest.mark.parametrize("stream", [False, True])
async def test_invalid_second_call_never_publishes_or_executes_first_call(
    tmp_path, monkeypatch, stream
):
    terminal = {
        "status": "completed",
        "output": [
            {
                "type": "function_call",
                "name": "write_file",
                "call_id": "first",
                "arguments": '{"path":"bad.txt","content":"bad"}',
            },
            {
                "type": "function_call",
                "name": "write_file",
                "call_id": "second",
                "arguments": '{"path":',
            },
        ],
        "usage": {"input_tokens": 10, "output_tokens": 2},
    }

    def handler(_):
        if stream:
            return httpx.Response(
                200,
                content="data: "
                + json.dumps({"type": "response.completed", "response": terminal})
                + "\n\n",
            )
        return httpx.Response(200, json=terminal)

    install_provider(monkeypatch, handler)
    runtime = Runtime(
        tmp_path, Settings(auth="none", stream=stream, approval="auto", memory_enabled=False)
    )
    state = await runtime.run(TaskSpec(prompt="Write a file"))
    assert state.phase == "failed" and state.stop_reason == "provider_error"
    assert state.step == 0 and not (tmp_path / "bad.txt").exists()
    assert not any(item.get("type") == "function_call" for item in runtime._structured_history)
    assert state.usage["total_tokens"] == 12


def test_attribution_is_bounded_and_contradictions_are_visible():
    from aster.providers.usage import _numeric_usage, _usage_warnings

    raw = {
        "input_tokens": 10,
        "output_tokens": 2,
        "attribution": {
            "items": {"sensitive-id": {"input_tokens": 1}},
            "request_fields": {"instructions": {"input_tokens": 1}, "tools": {"input_tokens": 2}},
        },
    }
    assert "input_attribution_disagrees" in _usage_warnings(
        _numeric_usage(raw), normalize_usage(raw)
    )
    raw["attribution"]["items"] = {
        f"private-{i}": {"input_tokens": i, "secret": "private"} for i in range(1000)
    }
    safe = _numeric_usage(raw)
    assert safe["attribution"]["items_count"] == 1000
    assert len(safe["attribution"]["items"]) == 128
    assert "private" not in json.dumps(safe)
    assert "input_attribution_disagrees" not in _usage_warnings(safe, normalize_usage(raw))


async def test_nonstream_diagnostic_observer_cannot_mutate_reply_or_leak_failure(
    monkeypatch, caplog
):
    install_provider(monkeypatch, lambda _: httpx.Response(200, json={"output_text": "done"}))

    def broken(event):
        event["usage"]["usage_requests"] = 999
        raise RuntimeError("sensitive-observer-detail")

    reply = await ModelClient(Settings(auth="none")).complete([], [], on_event=broken)
    assert reply.text == "done" and reply.usage == {"usage_requests": 1}
    assert reply.diagnostics["usage"] == {"usage_requests": 1}
    assert reply.diagnostics["usage_observed"] is False
    assert "sensitive-observer-detail" not in caplog.text


@pytest.mark.parametrize("stream", [False, True])
async def test_cancellation_after_billed_empty_attempt_retains_usage_once(monkeypatch, stream):
    requests, events = [], []
    entered = asyncio.Event()

    async def handler(request):
        requests.append(request)
        if len(requests) > 1:
            entered.set()
            await asyncio.Event().wait()
        terminal = {
            "status": "completed",
            "output": [],
            "usage": {"input_tokens": 10, "output_tokens": 2},
        }
        if stream:
            return httpx.Response(
                200,
                content="data: "
                + json.dumps({"type": "response.completed", "response": terminal})
                + "\n\n",
            )
        return httpx.Response(200, json=terminal)

    install_provider(monkeypatch, handler)
    client = ModelClient(Settings(auth="none", stream=stream))
    task = asyncio.create_task(client.complete([], [], on_event=events.append))
    await asyncio.wait_for(entered.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(requests) == 2
    assert client.last_usage["total_tokens"] == 12 and client.last_usage["usage_requests"] == 1
    diagnostics = [row for row in events if row["type"] == "diagnostic"]
    assert [row["classification"] for row in diagnostics] == ["empty_response", "cancelled"]
    assert diagnostics[0]["usage"]["total_tokens"] == 12
    assert diagnostics[1]["usage"] == {} and diagnostics[1]["usage_observed"] is False
