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
