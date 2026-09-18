"""Offline driver smoke test. Stub output is never counted as a product result."""

import importlib.util
import json
import subprocess
from argparse import Namespace
from pathlib import Path

import pytest

from aster.config import Settings
from aster.schema import ModelReply, ToolCall

SPEC = importlib.util.spec_from_file_location(
    "project_driver", Path(__file__).with_name("run_project.py")
)
driver = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(driver)


async def test_real_runtime_driver_persists_usage_and_refuses_overwrite(tmp_path, monkeypatch):
    # Keep a real Runtime and one real read_file tool; replace only the network response.
    experiment = tmp_path / "evaluations" / "probe"
    experiment.mkdir(parents=True)
    (experiment / "rules").mkdir()
    (experiment / "rules" / "rule.md").write_text("Public fixture rule.\n")
    for name in ("PROTOCOL.md", "check_browser.py", "prompt.md", "run_project.py"):
        (experiment / name).write_text("Offline driver fixture\n")
    monkeypatch.setattr(driver, "HERE", experiment)
    monkeypatch.setattr(driver, "__file__", str(experiment / "run_project.py"))
    original_repo = driver.REPO
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=original_repo, text=True
    ).strip()

    async def complete(self, messages, tools, *, on_event=None):
        if not self.requests:
            return ModelReply(
                calls=[ToolCall(name="read_file", arguments={"path": "docs/rule.md"})]
            )
        return ModelReply(
            text="Public fixture rule.", usage={"input_tokens": 12}, provider_model="offline"
        )

    monkeypatch.setattr(driver.ModelClient, "complete", complete)
    monkeypatch.setattr(driver, "load_settings", lambda *args, **kwargs: Settings(**kwargs))
    # Paths in results are repository-relative; test artifacts live in a temporary tree.
    original_relative_to = Path.relative_to

    def relative_to(path, *other, **kwargs):
        if path.is_relative_to(tmp_path) and other == (original_repo,):
            return original_relative_to(path, tmp_path)
        return original_relative_to(path, *other, **kwargs)

    monkeypatch.setattr(Path, "relative_to", relative_to)
    args = Namespace(
        name="offline",
        source_commit=commit,
        seed=None,
        skill=False,
        memory_seed=None,
        prompt=experiment / "prompt.md",
        read_only=True,
        memory=False,
        steps=3,
        turns=3,
        seconds=30,
        feedback=0,
    )
    assert await driver.run(args) == 0
    result = json.loads((experiment / "results/offline.json").read_text())
    assert result["read_file_calls"] == 1
    assert result["requests"][1]["response_model"] == "offline"
    assert result["requests"][1]["usage"]["input_tokens"] == 12
    assert result["docs_unchanged"] and result["source_unchanged"]
    assert result["operator_candidate_edits"] == 0
    with pytest.raises(ValueError, match="overwrite"):
        await driver.run(args)
