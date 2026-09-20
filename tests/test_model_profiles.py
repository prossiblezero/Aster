"""Named models resolve atomically without changing execution permissions or leaking keys."""

import json

import pytest
from pydantic import ValidationError

from aster.config import ModelProfile, load_settings
from aster.schema import ModelReply
from aster.ui.app import AsterTui

PROFILES = """
approval = "never"
read_only = true
write_scope = ["src"]
network = false

[profiles.remote]
provider = "gateway"
model = "gateway-model"
protocol = "responses"
base_url = "https://gateway.example/v1"
api_key_env = "ASTER_PROFILE_TEST_KEY"
auth = "required"
context_tokens = 12000
output_tokens = 2048
stream = true

[profiles.local]
provider = "local"
model = "local-model"
protocol = "chat"
base_url = "http://127.0.0.1:8000/v1"
auth = "none"
context_tokens = 4096
output_tokens = 512
"""


def configuration(tmp_path, monkeypatch, *, key=True):
    for name in (
        "ASTER_PROTOCOL",
        "ASTER_MODEL",
        "ASTER_BASE_URL",
        "ASTER_API_KEY",
        "ASTER_PROFILE_TEST_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    config = tmp_path / "models.toml"
    config.write_text(PROFILES)
    env = tmp_path / ".env"
    env.write_text("ASTER_PROFILE_TEST_KEY=profile-synthetic-secret\n" if key else "")
    return config, env


def test_profile_selection_replaces_coordinates_auth_and_budgets_atomically(tmp_path, monkeypatch):
    config, env = configuration(tmp_path, monkeypatch)
    monkeypatch.setenv("ASTER_MODEL", "legacy-override")
    monkeypatch.setenv("ASTER_API_KEY", "legacy-synthetic-secret")
    settings = load_settings(env, config)
    assert settings.model == "legacy-override" and settings.profile is None
    remote = settings.select_profile("remote")
    assert (remote.provider, remote.model, remote.protocol, remote.base_url) == (
        "gateway",
        "gateway-model",
        "responses",
        "https://gateway.example/v1",
    )
    assert remote.profile == "remote" and remote.stream
    assert remote.api_key.get_secret_value() == "profile-synthetic-secret"
    assert (remote.effective_context_tokens, remote.effective_output_tokens) == (12000, 2048)
    local = remote.select_profile("local")
    assert local.profile == "local" and local.auth == "none" and not local.stream
    assert local.api_key.get_secret_value() == ""
    assert (local.context_tokens, local.output_tokens) == (4096, 512)
    for selected in (remote, local):
        assert selected.approval == "never" and selected.read_only
        assert selected.write_scope == ["src"] and not selected.network
        assert selected.sandbox == "required"
        serialized = selected.model_dump_json() + json.dumps(selected.identity()) + repr(selected)
        assert "profile-synthetic-secret" not in serialized
        assert "legacy-synthetic-secret" not in serialized
        assert "profiles" not in selected.identity()
        assert "_profile_credentials" not in serialized
    assert remote.api_key.get_secret_value() == "profile-synthetic-secret"
    assert settings.model == "legacy-override"
    assert config.read_text() == PROFILES


def test_startup_profile_precedence_and_explicit_cli_overrides(tmp_path, monkeypatch):
    config, env = configuration(tmp_path, monkeypatch)
    monkeypatch.setenv("ASTER_MODEL", "legacy-override")
    monkeypatch.setenv("ASTER_PROFILE_TEST_KEY", "process-synthetic-secret")
    config.write_text('profile = "local"\n' + PROFILES)
    assert load_settings(env, config).profile == "local"
    selected = load_settings(env, config, profile="remote", output_tokens=1024)
    assert selected.model == "gateway-model"
    assert selected.output_tokens == 1024 and selected.context_tokens == 12000
    assert selected.api_key.get_secret_value() == "process-synthetic-secret"
    assert load_settings(env, config, profile="local").api_key.get_secret_value() == ""


def test_profile_failure_leaves_configuration_unchanged(tmp_path, monkeypatch):
    config, env = configuration(tmp_path, monkeypatch, key=False)
    settings = load_settings(env, config, profile="local")
    before = settings.model_dump_json()
    with pytest.raises(ValueError, match="unknown model profile"):
        settings.select_profile("missing")
    with pytest.raises(ValueError, match="missing profile credential"):
        settings.select_profile("remote")
    assert settings.model_dump_json() == before
    with pytest.raises(ValidationError, match="reserve output_tokens"):
        load_settings(env, config, profile="local", context_tokens=1024, output_tokens=1024)
    assert settings.model_dump_json() == before


@pytest.mark.parametrize(
    "change,error",
    [
        ({"auth": "required"}, "api_key_env"),
        ({"api_key_env": "KEY"}, "auth=none"),
        ({"base_url": "https://user:password@example.test/v1"}, "without credentials"),
        (
            {"base_url": "https://example.test/v1?api_key=synthetic-inline-secret"},
            "without credentials",
        ),
        ({"api_key": "synthetic-inline-secret"}, "Extra inputs"),
        ({"network": True}, "Extra inputs"),
        ({"context_tokens": 1024, "output_tokens": 1024}, "reserve output_tokens"),
        ({"protocol": "chat", "stream": True}, "only the responses"),
        ({"context_tokens": None}, "valid integer"),
    ],
)
def test_profile_rejects_invalid_or_permission_bearing_config_without_echoing_secrets(
    change, error
):
    with pytest.raises(ValidationError, match=error) as caught:
        ModelProfile.model_validate(
            dict(
                provider="local",
                model="local",
                protocol="responses",
                base_url="http://localhost:8000/v1",
                auth="none",
                context_tokens=4096,
                output_tokens=512,
            )
            | change
        )
    assert "synthetic-inline-secret" not in str(caught.value)


def test_cli_selects_profile_and_reports_effective_configuration_without_credentials(
    tmp_path, monkeypatch, capsys
):
    from aster import cli

    config, env = configuration(tmp_path, monkeypatch)
    received = []

    class NoNetworkTui:
        def __init__(self, root, settings):
            received.append(settings)

        def run(self):
            pass

    monkeypatch.setattr(cli, "AsterTui", NoNetworkTui)
    assert (
        cli.main(["--tui", "--config", str(config), "--env-file", str(env), "--profile", "remote"])
        == 0
    )
    assert received[0].profile == "remote"
    output = capsys.readouterr().err
    assert output == ""  # The TUI renders its own Chinese model/budget display.
    assert received[0].effective_context_tokens == 12000
    assert received[0].effective_output_tokens == 2048
    assert "profile-synthetic-secret" not in output
    with pytest.raises(SystemExit) as caught:
        cli.main(["--tui", "--config", str(config), "--env-file", str(env), "--profile", "missing"])
    assert caught.value.code == 2
    assert len(received) == 1


class FinishModel:
    def __init__(self):
        self.requests = []

    async def complete(self, messages, tools):
        self.requests.append(messages)
        return ModelReply(text="Finished.")


async def test_tui_model_commands_stay_out_of_tasks_and_fail_without_polluting_state(
    tmp_path, monkeypatch
):
    config, env = configuration(tmp_path, monkeypatch)
    app = AsterTui(tmp_path, load_settings(env, config, profile="local", completion="off"))
    model = FinishModel()
    app.runtime.model = model
    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt")
        prompt.value = "/model"
        await pilot.press("enter")
        assert app.runtime.state is None and app.task_worker is None and model.requests == []
        original = app.settings
        prompt.value = "/model missing"
        await pilot.press("enter")
        assert app.settings is original and app.runtime.settings is original
        assert app.runtime.state is None and app.task_worker is None
        prompt.value = "/model remote"
        await pilot.press("enter")
        assert app.settings.profile == "remote" and app.runtime.settings is app.settings
        assert app.runtime.state is None and app.task_worker is None and model.requests == []
        assert "gateway-model" in app.sub_title
        assert not prompt.disabled


@pytest.mark.parametrize("protocol", ["responses", "chat", "anthropic"])
def test_interactive_stream_default_respects_protocol_and_explicit_opt_out(
    tmp_path, monkeypatch, protocol
):
    for name in ("ASTER_PROTOCOL", "ASTER_MODEL", "ASTER_BASE_URL", "ASTER_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    assert load_settings(interactive=True, protocol=protocol).stream == (protocol == "responses")
    assert not load_settings(protocol=protocol).stream
    assert not load_settings(interactive=True, protocol=protocol, stream=False).stream
    config = tmp_path / "models.toml"
    config.write_text("""
profile = "primary"
[profiles.primary]
provider = "local"
model = "local-model"
protocol = "responses"
base_url = "http://localhost:8000/v1"
auth = "none"
context_tokens = 4096
output_tokens = 512
[profiles.buffered]
provider = "local"
model = "local-model"
protocol = "responses"
base_url = "http://localhost:8000/v1"
auth = "none"
context_tokens = 4096
output_tokens = 512
stream = false
""")
    settings = load_settings(config_file=config, interactive=True)
    assert settings.stream and not settings.select_profile("buffered").stream
    assert not load_settings(config_file=config, interactive=True, stream=False).stream
