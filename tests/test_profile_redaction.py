"""Previously selected credentials remain protected in provider and Runtime errors."""

import json

import httpx
import pytest

from aster.config import ModelProfile, Settings
from aster.provider import ModelClient, ProviderError
from aster.runtime import Runtime
from aster.schema import ModelReply, TaskSpec

OLD_KEY = "old-synthetic-profile-secret"
NEW_KEY = "new-synthetic-profile-secret"


async def test_nonstream_error_redacts_all_previously_selected_credentials(monkeypatch):
    original = httpx.AsyncClient
    monkeypatch.setattr(
        "aster.providers.client.httpx.AsyncClient",
        lambda **kwargs: original(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    401,
                    json={"error": {"message": f"Rejected {OLD_KEY} and {NEW_KEY}"}},
                )
            ),
            **kwargs,
        ),
    )
    model = ModelClient(Settings(api_key=NEW_KEY))
    model.redaction_secrets = [OLD_KEY, NEW_KEY]
    with pytest.raises(ProviderError) as caught:
        await model.complete([], [])
    rendered = str(caught.value) + json.dumps(caught.value.details)
    assert OLD_KEY not in rendered and NEW_KEY not in rendered
    assert "[REDACTED]" in rendered


def switched_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("ASTER_TEST_NEXT_PROFILE_KEY", NEW_KEY)
    runtime = Runtime(
        tmp_path,
        Settings(
            api_key=OLD_KEY,
            memory_enabled=False,
            max_workers=0,
            profiles={
                "next": ModelProfile(
                    provider="test-provider",
                    model="test-model",
                    protocol="responses",
                    base_url="https://example.test/v1",
                    api_key_env="ASTER_TEST_NEXT_PROFILE_KEY",
                    context_tokens=12000,
                    output_tokens=2048,
                )
            },
        ),
    )
    runtime.switch_model("next")
    return runtime


async def test_runtime_feedback_redacts_errors_after_profile_switch(tmp_path, monkeypatch):
    runtime = switched_runtime(tmp_path, monkeypatch)

    class FailingProvider:
        async def complete(self, *_):
            raise ProviderError(f"Rejected {OLD_KEY} and {NEW_KEY}", category="transport")

    runtime.model = FailingProvider()
    state = await runtime.run(TaskSpec(prompt="Inspect the workspace"))
    assert state.stop_reason == "provider_error"
    assert OLD_KEY not in state.feedback and NEW_KEY not in state.feedback
    assert "[REDACTED]" in state.feedback


async def test_runtime_final_redacts_both_keys_after_profile_switch(tmp_path, monkeypatch):
    runtime = switched_runtime(tmp_path, monkeypatch)

    class EchoProvider:
        async def complete(self, *_):
            return ModelReply(text=f"Answer containing {OLD_KEY} and {NEW_KEY}.")

    runtime.model = EchoProvider()
    state = await runtime.run(TaskSpec(prompt="Answer briefly"))
    assert state.phase == "completed"
    assert state.final == "Answer containing [REDACTED] and [REDACTED]."
    persisted = json.dumps(runtime.evidence.events(state.run_id))
    checkpoint = runtime.evidence.path("runs", state.run_id, "checkpoint.json").read_text()
    assert OLD_KEY not in persisted + checkpoint and NEW_KEY not in persisted + checkpoint
