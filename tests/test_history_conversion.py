import json
from copy import deepcopy
from itertools import permutations

import pytest

from aster.history import canonicalize
from aster.provider import ContextHistoryError, history_groups, request_payload


def transcript(protocol):
    calls = [
        {"id": "read-a", "name": "read_file", "arguments": {"path": "a.txt"}},
        {"id": "read-b", "name": "read_file", "arguments": {"path": "b.txt"}},
    ]
    results = ['{"status":"success","content":"alpha"}', '{"status":"cancelled"}']
    history = [{"role": "system", "content": "policy"}, {"role": "user", "content": "inspect"}]
    if protocol == "responses":
        history += [
            {"type": "reasoning", "id": "opaque-id", "encrypted_content": "private-signature"},
            {
                "type": "message",
                "id": "message-id",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "Reading.", "annotations": []}],
            },
            *[
                {
                    "type": "function_call",
                    "call_id": call["id"],
                    "name": call["name"],
                    "arguments": json.dumps(call["arguments"]),
                }
                for call in calls
            ],
            *[
                {"type": "function_call_output", "call_id": call["id"], "output": result}
                for call, result in zip(calls, results, strict=True)
            ],
        ]
    elif protocol == "chat":
        history += [
            {
                "role": "assistant",
                "content": "Reading.",
                "reasoning_content": "private-reasoning",
                "tool_calls": [
                    {
                        "type": "function",
                        "id": call["id"],
                        "function": {
                            "name": call["name"],
                            "arguments": json.dumps(call["arguments"]),
                        },
                    }
                    for call in calls
                ],
            },
            *[
                {"role": "tool", "tool_call_id": call["id"], "content": result}
                for call, result in zip(calls, results, strict=True)
            ],
        ]
    else:
        history += [
            {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "private-reasoning", "signature": "signature"},
                    {"type": "text", "text": "Reading."},
                    *[
                        {
                            "type": "tool_use",
                            "id": call["id"],
                            "name": call["name"],
                            "input": call["arguments"],
                        }
                        for call in calls
                    ],
                ],
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": call["id"], "content": result}
                    for call, result in zip(calls, results, strict=True)
                ],
            },
        ]
    return [*history, {"role": "user", "content": "Continue with the cancelled read."}]


@pytest.mark.parametrize("source,target", list(permutations(["responses", "chat", "anthropic"], 2)))
def test_six_directions_keep_calls_results_feedback_and_source_outside_wire(source, target):
    native = transcript(source)
    original = deepcopy(native)
    converted = canonicalize(
        native, provider="origin-provider", protocol=source, model="origin-model"
    )
    assert native == original
    assert converted.source == {
        "provider": "origin-provider",
        "protocol": source,
        "model": "origin-model",
    }
    assert converted.omitted_reasoning == 1
    payload = request_payload(converted.messages, [], protocol=target, model="target-model")
    wire = json.dumps(payload)
    assert "origin-provider" not in wire and "origin-model" not in wire
    assert "private-" not in wire and "signature" not in wire and "opaque-id" not in wire
    assert "Continue with the cancelled read." in wire and "Reading." in wire
    output = payload.get("input", payload.get("messages"))
    history_groups(output)
    if target == "responses":
        calls = [item for item in output if item.get("type") == "function_call"]
        results = [item for item in output if item.get("type") == "function_call_output"]
        assert [call["call_id"] for call in calls] == ["read-a", "read-b"]
        assert [json.loads(call["arguments"]) for call in calls] == [
            {"path": "a.txt"},
            {"path": "b.txt"},
        ]
        assert [item["call_id"] for item in results] == ["read-a", "read-b"]
        assert json.loads(results[1]["output"])["status"] == "cancelled"
    elif target == "chat":
        calls = [call for item in output for call in item.get("tool_calls", [])]
        assert [call["id"] for call in calls] == ["read-a", "read-b"]
        assert all(call["function"]["name"] == "read_file" for call in calls)
        results = [item for item in output if item["role"] == "tool"]
        assert [item["tool_call_id"] for item in results] == ["read-a", "read-b"]
        assert json.loads(results[1]["content"])["status"] == "cancelled"
    else:
        blocks = [block for item in output for block in item["content"]]
        calls = [block for block in blocks if block["type"] == "tool_use"]
        results = [block for block in blocks if block["type"] == "tool_result"]
        assert [call["id"] for call in calls] == ["read-a", "read-b"]
        assert [call["input"] for call in calls] == [{"path": "a.txt"}, {"path": "b.txt"}]
        assert [item["tool_use_id"] for item in results] == ["read-a", "read-b"]
        assert json.loads(results[1]["content"])["status"] == "cancelled"


@pytest.mark.parametrize("target", ["responses", "chat", "anthropic"])
def test_anthropic_tool_image_and_following_feedback_survive(target):
    image = {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": "YQ=="},
    }
    native = [
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "image", "name": "inspect_image", "input": {}}],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "image",
                    "content": [{"type": "text", "text": "observed"}, image],
                },
                {"type": "text", "text": "Please describe it."},
            ],
        },
    ]
    converted = canonicalize(native, protocol="anthropic")
    assert converted.messages[-1] == {"role": "user", "content": "Please describe it."}
    assert converted.messages[1]["output"] == [
        {"type": "input_text", "text": "observed"},
        {"type": "input_image", "image_url": "data:image/png;base64,YQ=="},
    ]
    wire = request_payload(converted.messages, [], protocol=target)
    assert "YQ==" in json.dumps(wire) and "observed" in json.dumps(wire)
    if target == "anthropic":
        assert wire["messages"][1]["content"][0]["content"][1] == image
    elif target == "chat":
        assert wire["messages"][-1]["content"] == [
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,YQ=="}}
        ]


@pytest.mark.parametrize("source,target", list(permutations(["responses", "chat", "anthropic"], 2)))
def test_tool_image_six_direction_conversion_keeps_text_and_image_bytes(source, target):
    data_url = "data:image/png;base64,YQ=="
    if source == "responses":
        history = [
            {
                "type": "function_call",
                "call_id": "image",
                "name": "inspect_image",
                "arguments": "{}",
            },
            {
                "type": "function_call_output",
                "call_id": "image",
                "output": [
                    {"type": "input_text", "text": "image evidence"},
                    {"type": "input_image", "image_url": data_url},
                ],
            },
        ]
    elif source == "chat":
        history = [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "type": "function",
                        "id": "image",
                        "function": {"name": "inspect_image", "arguments": "{}"},
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "image",
                "content": [
                    {"type": "text", "text": "image evidence"},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            },
        ]
    else:
        history = [
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "image", "name": "inspect_image", "input": {}}
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "image",
                        "content": [
                            {"type": "text", "text": "image evidence"},
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/png",
                                    "data": "YQ==",
                                },
                            },
                        ],
                    }
                ],
            },
        ]
    converted = canonicalize(history, protocol=source)
    payload = request_payload(converted.messages, [], protocol=target)
    wire = json.dumps(payload)
    assert "image evidence" in wire and "image/png" in wire and "YQ==" in wire
    history_groups(payload.get("input", payload.get("messages")))


def test_tool_error_and_refusal_keep_visible_meaning():
    native = [
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "failed", "name": "read_file", "input": {}}],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "failed",
                    "is_error": True,
                    "content": "access denied",
                }
            ],
        },
        {"role": "assistant", "content": None, "refusal": "Cannot continue."},
    ]
    converted = canonicalize(native, protocol="anthropic")
    assert json.loads(converted.messages[1]["output"]) == {
        "status": "error",
        "content": "access denied",
    }
    assert converted.messages[-1] == {"role": "assistant", "content": "Cannot continue."}


@pytest.mark.parametrize(
    "block",
    [
        {"type": "document", "source": {}},
        {"type": "input_audio", "data": "a"},
        {"type": "text", "text": "citation", "annotations": [{"url": "https://example.com"}]},
        {"type": "input_image", "image_url": "data:image/png;base64,YQ=="},
    ],
)
def test_unsupported_content_fails_without_mutating_input(block):
    history = [{"role": "user", "content": [block]}]
    original = deepcopy(history)
    with pytest.raises(ContextHistoryError, match="unsupported"):
        canonicalize(history, protocol="responses")
    assert history == original


@pytest.mark.parametrize(
    "history",
    [
        [
            {
                "type": "function_call",
                "call_id": "unfinished",
                "name": "read_file",
                "arguments": "{}",
            }
        ],
        [{"role": "tool", "tool_call_id": "orphan", "content": "result"}],
        [{"role": "user", "content": "task", "unknown_semantics": True}],
    ],
)
def test_incomplete_or_unknown_history_is_not_silently_repaired(history):
    with pytest.raises(ContextHistoryError):
        canonicalize(history, protocol="chat")


@pytest.mark.parametrize(
    "history",
    [
        [{"role": "assistant", "content": None, "tool_calls": "not-a-list"}],
        [{"role": "assistant", "content": None, "tool_calls": [None]}],
        [{"role": "user", "content": [{"type": []}]}],
        [{"role": "assistant", "content": None, "refusal": 17}],
        [
            {"type": "function_call", "call_id": "c", "name": "read_file", "arguments": "[]"},
            {"type": "function_call_output", "call_id": "c", "output": "text"},
        ],
    ],
)
def test_malformed_native_content_raises_domain_error(history):
    with pytest.raises(ContextHistoryError):
        canonicalize(history, protocol="responses")


def test_converting_canonical_messages_again_does_not_duplicate_or_drop_content():
    first = canonicalize(transcript("anthropic"), protocol="anthropic")
    second = canonicalize(first.messages, protocol="chat")
    assert second.messages == first.messages
    assert second.omitted_reasoning == 0
