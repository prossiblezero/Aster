"""Observed Responses trace tags must not block an idle same-protocol model switch."""

import hashlib
import json
from copy import deepcopy

import httpx
import pytest

from aster.config import ModelProfile, Settings
from aster.core.runtime import Runtime
from aster.messages import ContextHistoryError, canonicalize
from aster.providers.codec import request_payload
from aster.schema import TaskSpec


def native_history():
    # Shapes observed in the retained maintenance-initial checkpoint. Values are
    # synthetic: no candidate text, credentials, gateway IDs or reasoning copied.
    metadata = {
        "metadata": {"turn_id": "turn-fixture"},
        "internal_chat_message_metadata_passthrough": {
            "turn_id": "turn-fixture",
            "create_time": 1789745415.25,
        },
    }
    return [
        {"role": "user", "content": "Inspect then explain"},
        {
            "type": "reasoning",
            "id": "r1",
            "summary": [],
            "encrypted_content": "opaque",
            **deepcopy(metadata),
        },
        {
            "type": "function_call",
            "id": "f1",
            "call_id": "read-1",
            "name": "read_file",
            "arguments": '{"path":"source.txt"}',
            "status": "completed",
            **deepcopy(metadata),
        },
        {
            "type": "function_call_output",
            "call_id": "read-1",
            "output": '{"status":"success","content":"known result"}',
        },
        {
            "type": "message",
            "id": "m1",
            "role": "assistant",
            "status": "completed",
            "phase": "final_answer",
            "content": [
                {"type": "output_text", "text": "Inspected.", "annotations": [], "logprobs": []}
            ],
            **deepcopy(metadata),
        },
    ]


def test_observed_trace_tags_allow_idle_same_protocol_model_switch_and_keep_native_archive(
    tmp_path,
):
    profile = ModelProfile(
        provider="openai",
        protocol="responses",
        model="next-model",
        auth="none",
        base_url="http://offline.invalid/v1",
        context_tokens=128000,
        output_tokens=2048,
    )
    runtime = Runtime(tmp_path, Settings(auth="none", completion="off", profiles={"next": profile}))
    runtime.start(TaskSpec(prompt="Explain"))
    native = native_history()
    runtime._structured_history = deepcopy(native)
    runtime.finalize("Inspected.")
    session = runtime.state.session_id
    runtime.switch_model("next")
    assert runtime.state.session_id == session and runtime.settings.model == "next-model"
    assistant = next(item for item in reversed(runtime._structured_history)
                     if item.get("role") == "assistant")
    assert assistant["phase"] == "final_answer"
    wire = request_payload(
        runtime._structured_history, [], protocol="responses", model="next-model"
    )
    assert "turn-fixture" not in json.dumps(wire)
    assert "known result" in json.dumps(wire)
    assert [item["call_id"] for item in wire["input"] if "call_id" in item] == ["read-1", "read-1"]
    archived = runtime.evidence.path(*runtime._history_origins[-1]["artifact"].split("/"))
    assert json.loads(archived.read_text())["structured_history"] == native
    restored = Runtime(tmp_path, runtime.settings)
    restored.resume(runtime.state.run_id)
    assert restored.state.session_id == session
    assert restored._structured_history == runtime._structured_history


@pytest.mark.parametrize(
    "field,value",
    [
        ("metadata", {"turn_id": "known", "phase": "different"}),
        ("metadata", {"turn_id": "known", "arbitrary": "unknown semantics"}),
        ("metadata", {"turn_id": 123}),
        ("internal_chat_message_metadata_passthrough", {"turn_id": "known", "tool_arguments": {}}),
        (
            "internal_chat_message_metadata_passthrough",
            {"turn_id": "known", "create_time": float("inf")},
        ),
        ("internal_chat_message_metadata_passthrough", {"turn_id": "known", "create_time": True}),
        ("internal_chat_message_metadata_passthrough", "opaque unknown text"),
    ],
)
def test_unknown_or_malformed_gateway_metadata_still_fails_without_mutating_history(field, value):
    native = native_history()
    native[1][field] = value
    before = deepcopy(native)
    with pytest.raises(ContextHistoryError):
        canonicalize(native, protocol="responses")
    assert native == before


def test_trace_fields_on_non_responses_or_user_messages_are_not_silently_discarded():
    for protocol, item in [
        ("chat", {"role": "assistant", "content": "text", "metadata": {"turn_id": "known"}}),
        ("responses", {"role": "user", "content": "text", "metadata": {"turn_id": "known"}}),
    ]:
        with pytest.raises(ContextHistoryError):
            canonicalize([item], protocol=protocol)


async def test_request_archive_preserves_ciphertext_and_matches_wire_hash(tmp_path, monkeypatch):
    opaque = "prefix/sk-" + "abcdefghijklmnop"
    secret = "sk-" + "visiblecredential123456"
    runtime = Runtime(tmp_path, Settings(auth="none", memory_enabled=False, completion="off"))
    runtime.start(TaskSpec(prompt="Explain " + secret))
    runtime._structured_history = native_history()
    runtime._structured_history[1]["encrypted_content"] = opaque
    runtime._structured_history[3]["output"] = secret
    requests = []

    def handler(request):
        requests.append(request.content)
        return httpx.Response(200, json={"status": "completed", "output": [
            {"type": "reasoning", "id": "r2", "summary": [], "encrypted_content": opaque},
            {"type": "message", "role": "assistant",
             "content": [{"type": "output_text", "text": "Reviewed."}]},
        ]})

    original = httpx.AsyncClient
    monkeypatch.setattr("aster.providers.client.httpx.AsyncClient",
                        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    await runtime.turn()
    assert len(requests) == 1
    events = runtime.evidence.events(runtime.state.run_id)
    built = next(event for event in events if event["event"] == "context_built")
    reply = next(event for event in events if event["event"] == "model_reply")
    archived = json.loads((runtime.evidence.root / built["request_artifact"]).read_text())["payload"]
    assert httpx.Request("POST", "https://unused", json=archived).content == requests[0]
    assert next(item for item in archived["input"] if item.get("type") == "reasoning")["encrypted_content"] == opaque
    assert "[REDACTED]" in archived["input"][1]["content"]
    assert next(item for item in archived["input"] if item.get("type") == "function_call_output")["output"] == "[REDACTED]"
    assert secret not in json.dumps(archived)
    request_hash = hashlib.sha256(json.dumps(archived, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    assert built["request_sha256"] == reply["request_sha256"] == request_hash
    assert reply["diagnostics"]["payload_sha256"] == hashlib.sha256(requests[0]).hexdigest()
    assert reply["diagnostics"]["output_structure"][0]["encrypted_content_length"] == len(opaque)
    assert opaque not in json.dumps(events)
