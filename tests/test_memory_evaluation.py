"""Interrupted evaluations must retain failed attempts and their unknown usage."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aster.config import Settings

REPO = Path(__file__).resolve().parents[1]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, REPO / "evaluations/memory" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_resume_grades_saved_failed_reuse_without_repeating_or_resetting_it(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(REPO / "evaluations/memory"))
    runner = load("memory_runner_test", "run.py")
    case = dict(id="resume", focus="preserve failure", files={}, learn=["learn"],
                reuse="reuse", reuse_files={"product.py": "reset"})
    monkeypatch.setattr(runner, "CASES", [case])
    monkeypatch.setattr(runner, "load_settings", lambda *a, **k: Settings())
    episode = AsyncMock(side_effect=AssertionError("saved attempt was repeated"))
    monkeypatch.setattr(runner, "episode", episode)
    monkeypatch.setattr(runner, "grade", AsyncMock(return_value={"passed": True}))
    args = SimpleNamespace(arm="cost", work=tmp_path / "work", output=tmp_path / "results", case=None)
    root = args.work / args.arm / case["id"]
    root.mkdir(parents=True)
    (root / "product.py").write_text("candidate")
    target = args.output / args.arm / case["id"]
    runner.save(target / "learn-1.json", dict(phase="completed", memory_after=[], total_tokens=4,
                                             usage_coverage={"total_tokens": {"complete": True}}))
    runner.save(target / "reuse.json", dict(phase="failed", total_tokens=None, recalled_ids=[], tools=[],
                                           usage_coverage={"total_tokens": {"complete": False}}))
    original = (target / "reuse.json").read_bytes()
    await runner.main(args)
    episode.assert_not_awaited()
    assert (target / "reuse.json").read_bytes() == original
    assert (root / "product.py").read_text() == "candidate"
    result = json.loads((target / "result.json").read_text())
    assert result["passed"] is False and result["usage_complete"] is False


def test_unknown_usage_cannot_be_reported_as_a_discount():
    summarizer = load("memory_summary_test", "summarize.py")
    known = dict(uncached=100, cached=0, output=0, unknown=0)
    unknown = dict(uncached=0, cached=0, output=0, unknown=1)
    summary = {arm: {"cost_components": {"all": parts}}
               for arm, parts in (("baseline", known), ("cost", unknown))}
    rows = summarizer.cost_sensitivity(summary)
    assert all(row["baseline"] == 100 and row["cost"] is None and row["change_percent"] is None for row in rows)
    summary["baseline"]["cost_components"]["all"] = unknown
    assert all(row["change_percent"] is None for row in summarizer.cost_sensitivity(summary))
