import asyncio
import subprocess
import sys

import pytest

from aster.cli import Terminal, tui_main
from aster.config import Settings


def test_tui_entrypoint_forwards_command_line(monkeypatch):
    seen = []
    monkeypatch.setattr("aster.cli.main", lambda args: seen.append(args) or 0)
    monkeypatch.setattr(sys, "argv", ["aster-tui", "--cwd", "/tmp/repo", "--read-only"])
    assert tui_main() == 0
    assert seen == [["--tui", "--cwd", "/tmp/repo", "--read-only"]]


def test_terminal_null_input_is_unavailable(monkeypatch):
    with open("/dev/null") as stream:
        monkeypatch.setattr(sys, "stdin", stream)
        assert asyncio.run(Terminal(Settings()).question("Task?")) is None


def test_bare_cli_opens_tui_with_current_directory_env(tmp_path, monkeypatch):
    from aster import cli

    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("ASTER_MODEL=startup-test-model\n")
    monkeypatch.delenv("ASTER_MODEL", raising=False)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    seen = []

    class FakeTui:
        def __init__(self, root, settings, **limits):
            seen.append((root.resolve(), settings.model, limits))

        def run(self):
            seen.append("started")

    monkeypatch.setattr(cli, "AsterTui", FakeTui)
    assert cli.main([]) == 0
    assert seen == [(tmp_path, "startup-test-model", {}), "started"]


def test_installed_cli_smoke(tmp_path):
    from pathlib import Path

    for command in ("aster", "aster-tui"):
        completed = subprocess.run(
            [str(Path(sys.prefix) / "bin" / command), "--help"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert completed.returncode == 0, completed.stderr
        assert "--cwd" in completed.stdout

    completed = subprocess.run(
        [str(Path(sys.prefix) / "bin" / "aster")],
        cwd=tmp_path,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert completed.returncode == 2
    assert not completed.stdout
    assert "interactive terminal" in completed.stderr


def test_cli_stream_previews_before_completion_without_polluting_stdout(
    tmp_path, monkeypatch, capsys
):
    from aster import cli
    from aster.provider import ModelClient
    from aster.runtime import Runtime
    from aster.schema import ModelReply

    secret = "private-stream-credential"
    settings = Settings(stream=True, completion="off", memory_enabled=False, api_key=secret)
    captured = []

    class StreamingModel(ModelClient):
        async def complete(self, _messages, _tools, *, on_event=None):
            on_event({"type": "begin", "attempt": 1})
            on_event({"type": "text_delta", "text": "Incomplete attempt\n"})
            on_event({"type": "error", "attempt": 1})
            on_event({"type": "begin", "attempt": 2})
            on_event({"type": "text_delta", "text": "Reading the project.\nprivate-stream-"})
            early = capsys.readouterr()
            assert early.out == ""
            assert "Reading the project." in early.err
            assert "preview abandoned" in early.err and "attempt 2" in early.err
            assert "private-stream-" not in early.err
            captured.append(early.err)
            on_event({"type": "text_delta", "text": "credential\nLast paragraph"})
            on_event({"type": "completed", "attempt": 2})
            return ModelReply(text="The complete final answer.")

    def make_runtime(root, settings, **kwargs):
        return Runtime(root, settings, model=StreamingModel(settings), **kwargs)

    monkeypatch.setattr(cli, "Runtime", make_runtime)
    monkeypatch.setattr(cli, "load_settings", lambda *_a, **_k: settings)
    assert cli.main(["--cwd", str(tmp_path), "--stream", "Explain this project"]) == 0
    output = capsys.readouterr()
    assert output.out == "The complete final answer.\n"
    assert "[REDACTED]" in output.err and secret not in "".join(captured) + output.err
    assert "run_id=" in output.err and "verification=" in output.err


def test_cli_failure_prints_saved_run_and_resume_instructions_to_stderr(
    tmp_path, monkeypatch, capsys
):
    from aster import cli
    from aster.runtime import Runtime
    from aster.schema import ModelReply, ToolCall

    settings = Settings(completion="off", memory_enabled=False)

    class AlwaysReads:
        async def complete(self, *_args):
            return ModelReply(calls=[ToolCall(name="list_files", arguments={})])

    def make_runtime(root, settings, **kwargs):
        return Runtime(root, settings, model=AlwaysReads(), **kwargs)

    monkeypatch.setattr(cli, "Runtime", make_runtime)
    monkeypatch.setattr(cli, "load_settings", lambda *_a, **_k: settings)
    assert cli.main(["--cwd", str(tmp_path), "--max-turns", "1", "Inspect the project"]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert "reason=turn_limit" in output.err and "budget was exhausted" in output.err
    assert "modified_files=0 verification=missing" in output.err
    assert "--resume" in output.err and "--config/--profile/--env-file" in output.err


@pytest.mark.parametrize("mode", [[], ["--tui"]])
def test_cli_forwards_budgets_to_tui_and_rejects_invalid_values(tmp_path, monkeypatch, mode):
    from aster import cli

    seen = []

    class FakeTui:
        def __init__(self, root, settings, **limits):
            seen.append((root, limits))

        def run(self):
            pass

    monkeypatch.setattr(cli, "AsterTui", FakeTui)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli, "load_settings", lambda *_a, **_k: Settings())
    assert (
        cli.main(["--cwd", str(tmp_path), *mode, "--max-steps", "70", "--max-turns", "40"]) == 0
    )
    assert seen == [(tmp_path, {"max_steps": 70, "max_turns": 40})]
    for flag, value in (("--max-steps", "0"), ("--max-turns", "501")):
        with pytest.raises(SystemExit) as caught:
            cli.main([*mode, flag, value])
        assert caught.value.code == 2


def test_tui_rejects_prompt_instead_of_ignoring_it(monkeypatch, capsys):
    from aster import cli

    monkeypatch.setattr(cli, "load_settings", lambda *_a, **_k: pytest.fail("loaded settings"))
    with pytest.raises(SystemExit) as caught:
        cli.main(["--tui", "Implement this task"])
    assert caught.value.code == 2
    assert "--tui does not accept a prompt" in capsys.readouterr().err


def test_rl_smoke_without_prompt_keeps_default_task(tmp_path, monkeypatch):
    from aster import cli

    async def smoke(root, settings, prompt):
        assert root == tmp_path and prompt == "inspect the workspace"
        return 0

    monkeypatch.setattr(cli, "load_settings", lambda *_a, **_k: Settings())
    monkeypatch.setattr(cli, "_rl_smoke", smoke)
    assert cli.main(["--cwd", str(tmp_path), "--rl-smoke"]) == 0


def test_terminal_question_uses_stderr_not_final_answer_stream(tmp_path, monkeypatch, capsys):
    answer = tmp_path / "answer.txt"
    answer.write_text("Use a local web app\n")
    with answer.open() as stream:
        monkeypatch.setattr(sys, "stdin", stream)
        assert (
            asyncio.run(Terminal(Settings()).question("What kind of interface?"))
            == "Use a local web app"
        )
    output = capsys.readouterr()
    assert not output.out and "What kind of interface?" in output.err
