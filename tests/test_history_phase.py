"""Responses phase keeps progress distinct from final answers across session changes."""

import json
from copy import deepcopy

import pytest

from aster.config import ModelProfile, Settings
from aster.core.runtime import Runtime
from aster.messages import ContextHistoryError, canonicalize
from aster.providers.codec import request_payload
from aster.schema import ModelReply, TaskSpec


def native_history():
    return [
        {"type": "reasoning", "id": "reasoning-id", "encrypted_content": "opaque-history"},
        {
            "type": "message",
            "id": "progress-id",
            "role": "assistant",
            "status": "completed",
            "phase": "commentary",
            "content": [
                {
                    "type": "output_text",
                    "text": "Checking the file.",
                    "annotations": [],
                    "logprobs": [],
                }
            ],
        },
        {
            "type": "function_call",
            "call_id": "read-id",
            "name": "read_file",
            "arguments": '{"path":"source.txt"}',
            "status": "completed",
        },
        {"type": "function_call_output", "call_id": "read-id", "output": "source evidence"},
        {
            "type": "message",
            "id": "final-id",
            "role": "assistant",
            "status": "completed",
            "phase": "final_answer",
            "content": [
                {
                    "type": "output_text",
                    "text": "Finished checking.",
                    "annotations": [],
                    "logprobs": [],
                }
            ],
        },
    ]


def profile(protocol="responses"):
    return ModelProfile(
        provider="openai",
        model="same-model",
        protocol=protocol,
        base_url="http://offline.invalid/v1",
        auth="none",
        context_tokens=64000,
        output_tokens=2048,
    )


def test_responses_phase_survives_canonicalization_and_request_without_native_mutation():
    native = native_history()
    original = deepcopy(native)
    converted = canonicalize(native, protocol="responses")
    again = canonicalize(converted.messages, protocol="responses")
    assert again.messages == converted.messages and native == original
    assert converted.omitted_reasoning == 1
    wire = request_payload(again.messages, [], protocol="responses")["input"]
    assert [item["phase"] for item in wire if "phase" in item] == ["commentary", "final_answer"]
    assert [item["call_id"] for item in wire if "call_id" in item] == ["read-id", "read-id"]
    assert "opaque-history" not in json.dumps(wire)


@pytest.mark.parametrize("phase", ["analysis", "", 1, [], {}, True])
def test_unknown_or_malformed_phase_is_not_silently_discarded(phase):
    with pytest.raises(ContextHistoryError, match="invalid_history_phase"):
        canonicalize(
            [{"role": "assistant", "content": "text", "phase": phase}], protocol="responses"
        )


@pytest.mark.parametrize("role", ["system", "user"])
def test_phase_requires_an_assistant_message(role):
    with pytest.raises(ContextHistoryError, match="invalid_history_phase"):
        canonicalize(
            [{"role": role, "content": "text", "phase": "commentary"}], protocol="responses"
        )


def test_null_phase_is_optional_but_empty_phased_message_retains_its_meaning():
    converted = canonicalize(
        [
            {"role": "assistant", "content": "old model", "phase": None},
            {"role": "assistant", "content": "", "phase": "commentary"},
        ],
        protocol="responses",
    )
    assert converted.messages == [
        {"role": "assistant", "content": "old model"},
        {"role": "assistant", "content": "", "phase": "commentary"},
    ]


@pytest.mark.parametrize("target", ["chat", "anthropic"])
def test_cross_protocol_phase_fails_preflight_without_changing_session(tmp_path, target):
    runtime = Runtime(tmp_path, Settings(memory_enabled=False, profiles={"next": profile(target)}))
    runtime.start(TaskSpec(prompt="Explain source evidence"))
    runtime._structured_history = native_history()
    state, settings, history = (
        runtime.state,
        runtime.settings,
        deepcopy(runtime._structured_history),
    )
    with pytest.raises(ContextHistoryError, match="unsupported_history_phase_for_protocol"):
        runtime.switch_model("next")
    assert runtime.state is state and runtime.settings is settings
    assert runtime._structured_history == history and runtime._history_origins == []
    assert not list(runtime.evidence.path("runs", state.run_id).glob("native-history-*.json"))


async def test_context_profile_switch_archive_resume_and_followup_preserve_phase(tmp_path):
    settings = Settings(
        model="same-model",
        auth="none",
        context_tokens=128000,
        output_tokens=2048,
        memory_enabled=False,
        memory_evolution=False,
        auto_dream=False,
        completion="off",
        profiles={"compact": profile()},
    )
    runtime = Runtime(tmp_path, settings)
    runtime.start(TaskSpec(prompt="Explain source evidence"))
    native = native_history()
    runtime._structured_history = deepcopy(native)
    runtime.finish("provider_error", failed=True)
    original_session = runtime.state.session_id
    runtime.switch_model("compact")
    assert runtime.settings.context_tokens == 64000
    assert runtime.settings.sandbox == "required" and runtime.settings.network is False
    archive = runtime.evidence.path(*runtime._history_origins[-1]["artifact"].split("/"))
    assert json.loads(archive.read_text())["structured_history"] == native
    restored = Runtime(tmp_path, runtime.settings)
    restored.resume(runtime.state.run_id)
    restored.continue_task("Explain the remaining details")

    class OfflineModel:
        async def complete(self, messages, tools):
            assert [item["phase"] for item in messages if "phase" in item] == [
                "commentary",
                "final_answer",
            ]
            assert "Explain the remaining details" in json.dumps(messages)
            assert "source evidence" in json.dumps(messages)
            return ModelReply(text="Synthetic followup complete")

    restored.model = OfflineModel()
    final = await restored.run()
    assert final.phase == "completed" and final.session_id == original_session
    assert restored._history_origins == runtime._history_origins
