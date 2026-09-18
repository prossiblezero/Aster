"""Offline checks of independent graders and measurement extraction."""

import importlib.util
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import benchmark_support
import pytest

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("memory_followup_driver", HERE / "run.py")
driver = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(driver)


@pytest.mark.parametrize("phase,expected", [(1, 7), (2, 8)])
async def test_seed_only_candidate_fails_every_independent_grader(tmp_path, phase, expected):
    root = tmp_path / "candidate"
    shutil.copytree(HERE / "seed", root)
    before = driver.files_snapshot(root)
    previous = benchmark_support.DATASETS
    result = await driver.grade(benchmark_support, root, phase)
    assert benchmark_support.DATASETS == previous
    assert not result["passed"]
    assert result["tests"]["executed"] == result["tests"]["expected"] == expected
    assert result["tests"]["passed"] < expected
    assert result["isolation"] == "landlock+seccomp"
    assert driver.files_snapshot(root) == before


def test_first_request_memory_extraction_uses_saved_actual_prompt(tmp_path):
    lesson = {"id": "lesson", "text": '默认便签目录为 "inbox_notes/"。'}
    artifact = tmp_path / "request.json"
    artifact.write_text(
        json.dumps(
            {
                "payload": {
                    "input": [
                        {"role": "system", "content": "policy"},
                        {"role": "user", "content": "不同的中文问题"},
                        {"role": "user", "content": "memory:\n" + lesson["text"]},
                    ]
                }
            },
            ensure_ascii=False,
        )
    )
    events = [
        {"event": "context_built", "request_artifact": "request.json"},
        {
            "event": "tool_result",
            "call": {"name": "tool_load", "arguments": {"names": ["memory_store"]}},
            "outcome": {"status": "success"},
        },
        {
            "event": "tool_result",
            "call": {"name": "memory_store", "arguments": {}},
            "outcome": {"status": "blocked"},
        },
    ]
    runtime = SimpleNamespace(
        memory=SimpleNamespace(state={"lessons": [lesson]}),
        state=SimpleNamespace(recalled_ids=["lesson"]),
        evidence=SimpleNamespace(path=lambda *parts: tmp_path.joinpath(*parts)),
    )
    result = driver.memory_metrics(runtime, {"lessons": [lesson]}, events)
    assert result["first_request_recalled_ids"] == ["lesson"]
    assert result["discover_successes"] == 1
    assert result["store_attempts"] == 1 and result["store_successes"] == 0
    assert result["project_read_events"] == []


async def test_existing_stage_rejects_task_retry_before_touching_candidate(tmp_path, monkeypatch):
    monkeypatch.setattr(driver, "HERE", tmp_path)
    destination = tmp_path / "off"
    destination.mkdir()
    (destination / "phase1.json").write_text("{}")
    args = SimpleNamespace(condition="off", phase=1)
    with pytest.raises(ValueError, match="repeat a task"):
        await driver.run_phase(args, None, None, {})
    assert not (destination / "candidate").exists()


async def test_grader_error_is_saved_without_mutating_dataset_root(tmp_path):
    async def broken(*_):
        raise OSError("synthetic grader failure")

    support = SimpleNamespace(DATASETS=tmp_path, external_check=broken)
    result = await driver.grade(support, tmp_path, 1)
    assert support.DATASETS == tmp_path
    assert result == {"passed": False, "status": "grader_error", "error": "OSError"}
