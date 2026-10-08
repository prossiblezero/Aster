"""Model defaults and explicit gateway caps remain distinct and checkpoint-safe."""

import pytest
from pydantic import ValidationError

from aster.config import MODEL_CONTEXT_LIMITS, Settings, load_settings


def test_memory_defaults_on_and_explicit_configuration_survives(tmp_path):
    fields = {
        "memory_enabled": True,
        "memory_evolution": True,
        "auto_dream": True,
        "max_workers": 0,
    }
    defaults = Settings()
    assert {name: getattr(defaults, name) for name in fields} == fields
    for interactive in (False, True):
        loaded = load_settings(interactive=interactive)
        assert {name: getattr(loaded, name) for name in fields} == fields
    config = tmp_path / "optional.toml"
    config.write_text(
        "memory_enabled = false\nmemory_evolution = false\nauto_dream = false\nmax_workers = 2\n"
    )
    disabled = load_settings(config_file=config)
    assert not disabled.memory_enabled and not disabled.memory_evolution and not disabled.auto_dream
    assert disabled.max_workers == 2
    restored = Settings.model_validate_json(disabled.model_dump_json())
    assert restored.identity() == disabled.identity() != defaults.identity()
    enabled = load_settings(config_file=config, memory_enabled=True, memory_evolution=True,
                            auto_dream=True, max_workers=0)
    assert enabled.memory_enabled and enabled.memory_evolution and enabled.auto_dream
    assert enabled.max_workers == 0


def test_verified_luna_default_reserves_output_and_token_estimation_margin():
    settings = Settings()
    assert settings.context_tokens is None
    assert settings.effective_context_tokens == 908668
    assert settings.effective_context_tokens - settings.effective_output_tokens == 875900
    assert settings.context_budget_source == "model_profile"
    assert settings.identity()["context_tokens"] == 908668
    assert settings.model_dump()["context_tokens"] is None


def test_explicit_gateway_cap_keeps_existing_total_budget_semantics(tmp_path):
    config = tmp_path / "gateway.toml"
    config.write_text("context_tokens = 4096\noutput_tokens = 512\n")
    settings = load_settings(config_file=config)
    assert settings.context_tokens == settings.effective_context_tokens == 4096
    assert settings.effective_context_tokens - settings.effective_output_tokens == 3584
    assert settings.context_budget_source == "explicit"


@pytest.mark.parametrize("model", ["custom-gateway-model", "gpt-5.6-luna-snapshot", "gpt-5.6-sol"])
def test_unverified_models_use_a_transparent_fallback_without_guessing_a_large_window(model):
    settings = Settings(model=model)
    assert settings.context_tokens is None and settings.effective_context_tokens == 24000
    assert settings.context_budget_source == "fallback"
    assert settings.identity()["context_tokens"] == 24000
    assert Settings(model=model, context_tokens=8000).effective_context_tokens == 8000


def test_automatic_budget_tracks_model_changes_and_serialization():
    settings = Settings()
    settings.model = "local-model"
    assert settings.effective_context_tokens == settings.identity()["context_tokens"] == 24000
    settings.model = "gpt-5.6-luna"
    assert settings.effective_context_tokens == 908668
    copied = settings.model_copy(update={"model": "local-model"})
    assert copied.effective_context_tokens == 24000
    restored = Settings.model_validate_json(copied.model_dump_json())
    restored.model = "gpt-5.6-luna"
    assert restored.effective_context_tokens == 908668


def test_identity_binds_effective_limits_and_excludes_credentials():
    automatic = Settings(api_key="fake-secret-credential")
    explicit = Settings(context_tokens=automatic.effective_context_tokens)
    assert automatic.identity() == explicit.identity()
    assert "api_key" not in automatic.identity() and "base_url" not in automatic.identity()
    assert "fake-secret-credential" not in str(automatic.identity())
    changed = Settings(context_tokens=automatic.effective_context_tokens - 100)
    assert changed.identity() != automatic.identity()


def test_a_profile_update_changes_checkpoint_identity_without_recreating_settings(monkeypatch):
    settings = Settings()
    previous = settings.identity()
    monkeypatch.setitem(
        MODEL_CONTEXT_LIMITS,
        "gpt-5.6-luna",
        {"window": 1_000_000, "input": 900_000, "output": 100_000},
    )
    assert settings.identity()["context_tokens"] == 887768
    assert settings.identity() != previous


def test_runtime_request_tracks_the_current_model_and_output_reserve(tmp_path):
    from aster.core.runtime import Runtime
    from aster.providers.codec import request_payload
    from aster.schema import TaskSpec

    settings = Settings()
    runtime = Runtime(tmp_path, settings)
    runtime.start(TaskSpec(prompt="Inspect files."))
    runtime.state.messages = [{"role": "tool", "content": "old observation " * 15000}]
    settings.model = "local-model"
    settings.output_tokens = 1024
    prompt, definitions, report = runtime.prepare_request()
    assert report.budget == settings.identity()["context_tokens"] - 1024 == 22976
    assert report.compacted and report.estimated_tokens <= report.budget
    assert (
        runtime.context.count(
            request_payload(
                prompt,
                definitions,
                protocol=settings.protocol,
                model=settings.model,
                output_tokens=1024,
            )
        )
        == report.estimated_tokens
    )


def test_luna_maximum_output_is_reserved_separately_from_input_allowance():
    settings = Settings(output_tokens=128000)
    assert settings.effective_context_tokens == 1003900
    assert settings.effective_context_tokens - settings.effective_output_tokens == 875900


@pytest.mark.parametrize(
    "overrides, error",
    [
        ({"output_tokens": 128001}, "model output limit"),
        ({"context_tokens": 925000, "output_tokens": 2048}, "model input/window limit"),
        ({"context_tokens": 1050001, "output_tokens": 128000}, "model input/window limit"),
        ({"context_tokens": 2048, "output_tokens": 2048}, "reserve output_tokens"),
        ({"model": "unknown", "output_tokens": 24000}, "reserve output_tokens"),
    ],
)
def test_impossible_budgets_are_rejected(overrides, error):
    with pytest.raises(ValidationError, match=error):
        Settings(**overrides)


def test_removed_single_tool_token_cap_is_rejected_instead_of_silently_ignored(tmp_path):
    with pytest.raises(ValidationError, match="tool_output_tokens"):
        Settings(tool_output_tokens=1600)
    config = tmp_path / "old.toml"
    config.write_text("tool_output_tokens = 1600\n")
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        load_settings(config_file=config)


@pytest.mark.parametrize(
    "model, expected",
    [
        (
            "gpt-5.6-luna",
            "875,900 输入 + 32,768 输出 token（模型默认预算，含估算余量）",
        ),
        ("local-model", "21,952 输入 + 2,048 输出 token（未知模型，使用保守预算）"),
    ],
)
async def test_tui_displays_effective_budget_and_unknown_model_fallback(tmp_path, model, expected):
    from aster.ui.app import AsterTui

    app = AsterTui(tmp_path, Settings(model=model))
    async with app.run_test(size=(120, 40)) as pilot:
        assert expected in str(app.query_one("#welcome").content)
        app.query_one("#prompt").value = "/context"
        await pilot.press("enter")
        text = "\n".join(line.text for line in app.query_one("#log").lines)
        assert expected in text
