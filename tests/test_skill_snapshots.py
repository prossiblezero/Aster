"""Selected skill text stays stable until an explicit, successful refresh."""

import hashlib
import json
from copy import deepcopy

import pytest

from aster.config import ModelProfile, Settings
from aster.context.prompt import CONTEXT_NOTE_PREFIX
from aster.core.runtime import Runtime
from aster.schema import ModelReply, TaskSpec, ToolCall


def setup_runtime(tmp_path, body="First version.\n", **settings):
    directory = tmp_path / ".aster" / "skills" / "guide"
    directory.mkdir(parents=True)
    source = directory / "SKILL.md"
    source.write_text(body)
    runtime = Runtime(tmp_path, Settings(memory_enabled=False, completion="off", **settings))
    runtime.start(TaskSpec(prompt="Inspect this project", max_steps=100))
    return runtime, source


async def load(runtime, **arguments):
    return await runtime.step(ToolCall(name="skill_load", arguments={"name": "guide", **arguments}))


def version(body):
    return hashlib.sha256(body.encode()).hexdigest()


def latest_section(prompt, name):
    prefix = CONTEXT_NOTE_PREFIX + name + ":\n"
    return next(item["content"][len(prefix):] for item in reversed(prompt)
                if item.get("role") == "user" and isinstance(item.get("content"), str)
                and item["content"].startswith(prefix))


async def test_selected_pages_and_model_body_do_not_reread_changed_source(tmp_path, monkeypatch):
    body = "首版说明。" * 30
    runtime, source = setup_runtime(tmp_path, body)
    prompt, _, _ = runtime.prepare_request()
    assert "Load skill_load with tool_load" in prompt[0]["content"]
    assert "Load skill_load with tool_load" not in prompt[-1]["content"]
    assert body not in prompt[-1]["content"]
    first = await load(runtime, limit=9)
    assert first.status == "success"
    source.write_text("SECOND VERSION MUST WAIT FOR REFRESH")

    def unexpected_read(*_args, **_kwargs):
        pytest.fail("selected skill body was reread without refresh")

    monkeypatch.setattr(runtime.skills, "load", unexpected_read)
    pieces = [first.content.split("\n", 1)[1]]
    offset = first.data["next_offset"]
    while offset is not None:
        page = await load(runtime, offset=offset, limit=19)
        assert page.status == "success", page
        pieces.append(page.content.split("\n", 1)[1])
        offset = page.data["next_offset"]
    assert "".join(pieces) == body
    prompt, _, _ = runtime.prepare_request()
    assert body in prompt[-1]["content"]
    assert "SECOND VERSION" not in prompt[-1]["content"]
    assert json.loads(latest_section(prompt, "workflow_state"))["active_skill_versions"] == {
        "guide": version(body)
    }


async def test_refresh_notifies_version_and_failure_keeps_selected_body(tmp_path):
    runtime, source = setup_runtime(tmp_path)
    first = await load(runtime)
    old_body = source.read_text()
    source.write_text("Replacement body.")
    out_of_range = await load(runtime, refresh=True, offset=1000)
    assert out_of_range.reason == "page_out_of_range"
    source.unlink()
    unavailable = await load(runtime, refresh=True)
    assert unavailable.status == "error"
    preserved = await load(runtime)
    assert preserved.content.split("\n", 1)[1] == old_body
    assert preserved.data["version"] == first.data["version"] == version(old_body)
    source.write_text("Replacement body.")
    refreshed = await load(runtime, refresh=True)
    assert refreshed.status == "success", refreshed
    assert refreshed.data["previous_version"] == version(old_body)
    assert refreshed.data["version"] == version("Replacement body.")
    assert refreshed.data["updated"] is True
    output = json.loads(runtime._structured_history[-1]["output"])
    assert output["previous_version"] == version(old_body)
    assert output["version"] == refreshed.data["version"] and output["updated"] is True
    same = await load(runtime, refresh=True)
    assert same.data["updated"] is False
    prompt, _, _ = runtime.prepare_request()
    assert "Replacement body." in prompt[-1]["content"]
    assert old_body not in prompt[-1]["content"]


async def test_resource_refresh_rejected_without_selection_or_source_read(tmp_path, monkeypatch):
    runtime, _ = setup_runtime(tmp_path)

    def unexpected_read(*_args, **_kwargs):
        pytest.fail("invalid resource refresh tried to read")

    monkeypatch.setattr(runtime.skills, "load", unexpected_read)
    result = await load(runtime, resource="references/checks.md", refresh=True)
    assert result.status == "blocked" and result.reason == "skill_resource_refresh"
    assert runtime.state.active_skills == []


async def test_snapshot_survives_resume_and_continuation_but_not_new_session(tmp_path):
    runtime, source = setup_runtime(tmp_path)
    await load(runtime)
    selected = source.read_text()
    source.write_text("New disk body.")
    runtime.finish("cancelled")
    restored = Runtime(tmp_path, runtime.settings)
    restored.resume(runtime.state.run_id)
    restored.continue_task("Continue inspecting")
    page = await load(restored)
    assert page.content.split("\n", 1)[1] == selected
    prompt, _, _ = restored.prepare_request()
    assert selected in prompt[-1]["content"] and "New disk body." not in prompt[-1]["content"]
    restored.new_session()
    restored.start(TaskSpec(prompt="Inspect again"))
    prompt, _, _ = restored.prepare_request()
    assert selected not in prompt[-1]["content"]
    assert "New disk body." not in prompt[-1]["content"]
    fresh = await load(restored)
    assert fresh.content.split("\n", 1)[1] == "New disk body."


async def test_legacy_checkpoint_prompts_explicit_load_without_reading_new_body(tmp_path):
    runtime, source = setup_runtime(tmp_path)
    await load(runtime)
    checkpoint = runtime.evidence.path("runs", runtime.state.run_id, "checkpoint.json")
    data = json.loads(checkpoint.read_text())
    data["state"].pop("skill_snapshots", None)
    checkpoint.write_text(json.dumps(data))
    source.write_text("NEW BODY NOT YET SELECTED")
    restored = Runtime(tmp_path, runtime.settings)
    restored.resume(runtime.state.run_id)
    prompt, _, _ = restored.prepare_request()
    assert "NEW BODY NOT YET SELECTED" not in prompt[-1]["content"]
    assert "skill_load" in prompt[-1]["content"] and "snapshot unavailable" in prompt[-1]["content"]
    selected = await load(restored)
    assert selected.content.split("\n", 1)[1] == "NEW BODY NOT YET SELECTED"


@pytest.mark.parametrize("entry", ["switch", "resume", "resume_same_identity"])
async def test_redacted_snapshot_hash_survives_new_credentials_and_restore(
    tmp_path, monkeypatch, entry
):
    old_secret, new_secret = "old-provider-credential-12345", "new-provider-credential-67890"
    monkeypatch.setenv("SKILL_NEXT_CREDENTIAL", new_secret)
    body = f"Instructions {old_secret} {new_secret} END"
    runtime, _ = setup_runtime(
        tmp_path,
        body,
        api_key=old_secret,
        profiles={
            "next": ModelProfile(
                provider="test",
                model="next-model",
                base_url="http://localhost:1234/v1",
                api_key_env="SKILL_NEXT_CREDENTIAL",
                protocol="chat",
                context_tokens=16000,
                output_tokens=512,
            )
        },
    )
    first = await load(runtime)
    first_body = body.replace(old_secret, "[REDACTED]")
    assert first.data["version"] == version(first_body)
    if entry == "switch":
        runtime.switch_model("next")
    else:
        settings = (
            runtime.settings.select_profile("next")
            if entry == "resume"
            else runtime.settings.model_copy(
                update={"api_key": runtime.settings.select_profile("next").api_key}
            )
        )
        restored = Runtime(tmp_path, settings)
        restored.resume(runtime.state.run_id, allow_model_change=entry == "resume")
        runtime = restored
    expected = first_body.replace(new_secret, "[REDACTED]")

    class InspectModel:
        called = False

        async def complete(self, messages, _tools):
            self.called = True
            actual = json.dumps(messages)
            assert old_secret not in actual and new_secret not in actual
            assert latest_section(messages, "active_skill_bodies") == "guide:\n" + expected
            assert json.loads(latest_section(messages, "workflow_state"))["active_skill_versions"] == {
                "guide": version(expected)
            }
            return ModelReply(text="Inspected.")

    runtime.model = InspectModel()
    await runtime.turn()
    assert runtime.model.called
    checkpoint = runtime.evidence.path("runs", runtime.state.run_id, "checkpoint.json")
    saved = json.loads(checkpoint.read_text())["state"]["skill_snapshots"]["guide"]
    assert saved == {"body": expected, "sha256": version(expected)}
    assert old_secret not in checkpoint.read_text() and new_secret not in checkpoint.read_text()
    restored = Runtime(tmp_path, runtime.settings)
    restored.resume(runtime.state.run_id)
    restored.continue_task("Continue")
    page = await load(restored)
    assert page.content.split("\n", 1)[1] == expected
    assert page.data["version"] == version(expected)


async def test_sensitive_skill_name_is_blocked_and_switch_preflight_preserves_session(
    tmp_path, monkeypatch
):
    secret = "future-model-credential-987654"
    monkeypatch.setenv("SKILL_NAME_CREDENTIAL", secret)
    runtime, source = setup_runtime(
        tmp_path,
        profiles={
            "next": ModelProfile(
                provider="test",
                model="test",
                protocol="chat",
                base_url="http://localhost:1234/v1",
                api_key_env="SKILL_NAME_CREDENTIAL",
                context_tokens=16000,
                output_tokens=512,
            )
        },
    )
    source.parent.rename(source.parent.with_name(secret))
    selected = await runtime.step(ToolCall(name="skill_load", arguments={"name": secret}))
    assert selected.status == "success"
    before, history = runtime.state.model_dump(), deepcopy(runtime._structured_history)
    settings, redactor = runtime.settings, runtime.evidence.redactor
    with pytest.raises(ValueError, match="skill_name_contains_credential"):
        runtime.switch_model("next")
    assert runtime.state.model_dump() == before and runtime._structured_history == history
    assert runtime.settings is settings and runtime.evidence.redactor is redactor

    restored = Runtime(tmp_path, settings.select_profile("next"))
    with pytest.raises(ValueError, match="skill_name_contains_credential"):
        restored.resume(runtime.state.run_id, allow_model_change=True)
    assert restored.state is None
    restored.start(TaskSpec(prompt="Inspect"))
    blocked = await restored.step(ToolCall(name="skill_load", arguments={"name": secret}))
    assert blocked.status == "blocked" and blocked.reason == "skill_name_contains_credential"
    assert restored.state.active_skills == [] and restored.state.skill_snapshots == {}


async def test_corrupt_snapshot_checkpoint_is_rejected(tmp_path):
    runtime, _ = setup_runtime(tmp_path)
    await load(runtime)
    checkpoint = runtime.evidence.path("runs", runtime.state.run_id, "checkpoint.json")
    data = json.loads(checkpoint.read_text())
    data["state"]["skill_snapshots"]["guide"]["body"] = "Tampered body"
    checkpoint.write_text(json.dumps(data))
    restored = Runtime(tmp_path, runtime.settings)
    with pytest.raises(ValueError, match="skill_snapshot_hash_mismatch"):
        restored.resume(runtime.state.run_id)


async def test_request_redacts_visible_state_but_preserves_native_opaque_bytes(tmp_path):
    secret = "synthetic-visible-state-credential"
    runtime, _ = setup_runtime(tmp_path, api_key=secret)
    runtime.state.task.prompt = "Inspect " + secret
    runtime.state.plan = "Plan " + secret
    runtime.state.messages.append({"role": "user", "content": secret})
    opaque = "sk-this-is-opaque-ciphertext"
    runtime._structured_history = [
        {"type": "reasoning", "encrypted_content": opaque},
        {"role": "assistant", "content": secret},
    ]

    class InspectModel:
        called = False

        async def complete(self, messages, _tools):
            self.called = True
            assert secret not in json.dumps(messages)
            assert (
                next(item for item in messages if item.get("type") == "reasoning")[
                    "encrypted_content"
                ]
                == opaque
            )
            return ModelReply(text="Inspected.")

    runtime.model = InspectModel()
    await runtime.turn()
    assert runtime.model.called


@pytest.mark.parametrize("collision", ["model", "tools"])
def test_request_control_credential_collision_is_rejected_before_sending(tmp_path, collision):
    # Do not redact identifiers or schemas into a different model/tool contract.
    secret = "synthetic-model-credential" if collision == "model" else "search"
    runtime, _ = setup_runtime(tmp_path, api_key=secret)
    if collision == "model":
        runtime.settings.model = secret
    with pytest.raises(ValueError, match="contains?_credential"):
        runtime.prepare_request()


@pytest.mark.parametrize("control", ["phase", "call_id"])
def test_request_history_controls_are_rejected_instead_of_rewritten(tmp_path, control):
    secret = "commentary" if control == "phase" else "synthetic-call-credential"
    runtime, _ = setup_runtime(tmp_path, api_key=secret)
    runtime._structured_history = (
        [{"role": "assistant", "phase": secret, "content": "Working"}]
        if control == "phase"
        else [
            {
                "type": "function_call",
                "name": "read_file",
                "call_id": secret,
                "arguments": '{"path":"a.txt"}',
            },
            {"type": "function_call_output", "call_id": secret, "output": "Read"},
        ]
    )
    before = deepcopy(runtime._structured_history)
    with pytest.raises(ValueError, match="history_control_contains_credential"):
        runtime.prepare_request()
    assert runtime._structured_history == before
