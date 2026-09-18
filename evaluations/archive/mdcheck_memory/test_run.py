"""Operator failures must preserve the other condition and the original project state."""

import asyncio
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
import run as operator

from aster.config import Settings


@pytest.fixture
def experiment(monkeypatch):
    runs = operator.HERE / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="operator-control-", dir=runs) as directory:
        root = Path(directory)
        monkeypatch.setattr(operator, "HERE", root)
        yield root


async def test_one_condition_error_does_not_cancel_the_other(experiment, monkeypatch, capsys):
    other_started = asyncio.Event()
    completed = []

    async def attempt(args, condition, dest, number):
        if condition == "memory_on":
            await other_started.wait()
            raise RuntimeError("sensitive-exception-value")
        other_started.set()
        await asyncio.sleep(0)
        completed.append(condition)
        operator.save(dest / "completed.json", {"passed": True})
        return True

    monkeypatch.setattr(operator, "run_attempt", attempt)
    args = SimpleNamespace(stage=1, condition="both")
    assert await asyncio.wait_for(operator.main_async(args), 2) == 1
    assert completed == ["memory_off"]
    assert (experiment / "runs/memory_off/stage-1-attempt-01/completed.json").is_file()
    error = experiment / "runs/memory_on/stage-1-attempt-01/operator-error.json"
    diagnostic = json.loads(error.read_text())
    assert diagnostic["type"] == "RuntimeError" and diagnostic["frames"]
    assert "sensitive-exception-value" not in error.read_text() + capsys.readouterr().out


async def test_configuration_drift_is_rejected_before_preparing_workspace(experiment, monkeypatch):
    project = experiment / "workspaces/memory_on"
    project.mkdir(parents=True)
    (project / "PROJECT.md").write_text("默认文档目录是 `docs`。\n")
    (project / "requirements.md").write_text("Stage 2 original requirements.\n")
    before = {path.name: path.read_bytes() for path in project.iterdir()}
    (experiment / "prompts").mkdir()
    (experiment / "prompts/03-conventions.md").write_text("Move the default to manual.\n")
    (experiment / "requirements.md").write_text("Stage 3 replacement requirements.\n")
    fixed = experiment / "runs/fixed-configuration.json"
    operator.save(fixed, {"model": "different-baseline"})
    fixed_before = fixed.read_bytes()
    monkeypatch.setattr(operator, "load_settings", lambda *_args, **kwargs: Settings(**kwargs))
    monkeypatch.setattr(operator, "product_manifest", lambda: {"src/aster/runtime.py": "frozen"})
    prepared = []
    original_prepare = operator.prepare_workspace

    def prepare(*args):
        prepared.append(args)
        return original_prepare(*args)

    monkeypatch.setattr(operator, "prepare_workspace", prepare)
    args = SimpleNamespace(stage=3, repair=None, model="test-model", context_tokens=24000,
                           output_tokens=4096, request_timeout=30, max_steps=10, max_turns=10)
    assert await operator.run_one(args, "memory_on") is False
    assert not prepared
    assert {path.name: path.read_bytes() for path in project.iterdir()} == before
    assert fixed.read_bytes() == fixed_before
    error = experiment / "runs/memory_on/stage-3-attempt-01/operator-error.json"
    assert json.loads(error.read_text())["type"] == "ValueError"
