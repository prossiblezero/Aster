"""Interrupted generation and UI observers must not manufacture successful actions."""

import asyncio
import json

import httpx
import pytest

from aster.config import Settings
from aster.evidence import EvidenceStore, Redactor
from aster.provider import ModelClient, ProviderError
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

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=response)

    original = httpx.AsyncClient
    monkeypatch.setattr(
        "aster.provider.httpx.AsyncClient",
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
        "aster.provider.httpx.AsyncClient",
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
    assert ModelClient._usage(raw) == {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}


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
        "aster.provider.httpx.AsyncClient",
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
