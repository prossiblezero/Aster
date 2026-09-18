import json

from textual.widgets import Input

from aster.config import Settings
from aster.runtime import Runtime
from aster.schema import ModelReply, TaskSpec
from aster.tui import AsterTui


class Conversation:
    def __init__(self):
        self.requests = []

    async def complete(self, messages, _tools):
        self.requests.append(messages)
        return ModelReply(text=f"Answer {len(self.requests)}")


async def test_tui_plain_followup_keeps_session_and_new_resets_it(tmp_path):
    app = AsterTui(
        tmp_path, Settings(completion="off", memory_enabled=False), max_steps=70, max_turns=45
    )
    model = Conversation()
    app.runtime.model = model
    async with app.run_test():
        await app._run_task("Original request")
        assert app.runtime.state.task.max_steps == 70
        assert app.runtime.state.task.max_turns == 45
        session = app.runtime.state.session_id
        await app._run_task("Explain your previous answer")
        assert app.runtime.state.task.max_steps == 70
        assert app.runtime.state.task.max_turns == 45
        assert app.runtime.state.session_id == session
        prompt = json.dumps(model.requests[-1])
        assert "Original request" in prompt and "Answer 1" in prompt
        assert "Explain your previous answer" in prompt
        input_widget = app.query_one("#prompt", Input)
        app.on_input_submitted(Input.Submitted(input_widget, "/new"))
        assert app.runtime.state is None
        await app._run_task("Independent request")
        assert app.runtime.state.task.max_steps == 70
        assert app.runtime.state.session_id != session
        prompt = json.dumps(model.requests[-1])
        assert "Original request" not in prompt and "/new" not in prompt


async def test_tui_resume_preserves_checkpoint_limits_without_cli_overrides(tmp_path):
    settings = Settings(completion="off", memory_enabled=False)
    original = Runtime(tmp_path, settings)
    original.start(TaskSpec(prompt="Explain this project", max_steps=12, max_turns=9))
    original.finish("provider_error", failed=True)
    app = AsterTui(tmp_path, settings)
    app.runtime.model = Conversation()
    async with app.run_test():
        input_widget = app.query_one("#prompt", Input)
        app.on_input_submitted(Input.Submitted(input_widget, f"/resume {original.state.run_id}"))
        await app.task_worker.wait()
        assert app.runtime.state.task.max_steps == 12
        assert app.runtime.state.task.max_turns == 9


def test_cli_resumes_without_a_prompt_and_with_feedback(tmp_path, monkeypatch, capsys):
    from aster import cli

    settings = Settings(completion="off", memory_enabled=False)
    original = Runtime(tmp_path, settings)
    original.start(TaskSpec(prompt="Original request", max_steps=7, max_turns=4))
    original.finish("provider_error", failed=True)
    model = Conversation()

    def make_runtime(root, settings, **kwargs):
        return Runtime(root, settings, model=model, **kwargs)

    monkeypatch.setattr(cli, "Runtime", make_runtime)
    monkeypatch.setattr(cli, "load_settings", lambda *_args, **_kwargs: settings)
    for feedback in [[], ["Explain the original requirement"]]:
        assert cli.main(["--cwd", str(tmp_path), "--resume", original.state.run_id, *feedback]) == 0
    assert "Original request" in json.dumps(model.requests[0])
    assert "Explain the original requirement" in json.dumps(model.requests[1])
    assert "Answer 2" in capsys.readouterr().out


def test_cli_missing_checkpoint_reports_failure_without_a_traceback(tmp_path, monkeypatch, capsys):
    from aster import cli

    monkeypatch.setattr(cli, "load_settings", lambda *_args, **_kwargs: Settings())
    assert cli.main(["--cwd", str(tmp_path), "--resume", "missing-run"]) == 1
    assert "No such file" in capsys.readouterr().err
