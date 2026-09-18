"""Offline cache-probe checks; no model request or candidate implementation."""

import asyncio
import importlib.util
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from aster.config import Settings
from aster.context import ContextBuilder
from aster.provider import request_payload
from aster.schema import RunState, TaskSpec
from aster.tools import definitions

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("cache_comparison_driver", HERE / "run.py")
driver = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(driver)


def request_pair():
    task = (HERE / "task.txt").read_text()
    state = RunState(task=TaskSpec(prompt=task, max_turns=3))
    builder = ContextBuilder(128000, 32768)
    tools = definitions(["read_file", "tool_load"])
    old, new = [], []
    for index in range(3):
        state.turn = index
        prompt, report = builder.build(state, task, tools=tools)
        assert not report.compacted
        new.append({"prompt": prompt, "tools": deepcopy(tools)})
        legacy = deepcopy(prompt)
        legacy[0]["content"] += "\nCurrent runtime mode: work."
        context = legacy.pop()
        context["content"] = context["content"].replace('"mode": "work", ', "")
        legacy.insert(1, context)
        old_tools = deepcopy(tools)
        old_tools[-1]["parameters"]["properties"]["names"]["items"]["enum"].remove(
            "observation_read"
        )
        old.append({"prompt": legacy, "tools": old_tools})
    return old, new


def test_allowed_layout_and_catalog_migration_preserves_controlled_content():
    old, new = request_pair()
    for before, after in zip(old, new, strict=True):
        assert driver.control_record(**before) == driver.control_record(**after)
    after["prompt"][1]["content"] += " An extra requirement."
    assert driver.control_record(**before) != driver.control_record(**after)


def test_prefix_proxy_covers_long_task_only_in_new_layout():
    old, new = request_pair()
    metrics = [
        driver.prefix_metrics([request_payload(row["prompt"], row["tools"]) for row in rows])
        for rows in (old, new)
    ]
    assert metrics[0]["all_three_shared_tokens"] < 1024
    assert metrics[1]["all_three_shared_tokens"] > 1024
    assert metrics[1]["all_three_shared_tokens"] > metrics[0]["all_three_shared_tokens"]


def test_usage_zero_and_unknown_are_not_confused():
    result = driver.summarize(
        [
            {"usage": {"input_tokens": 1200, "cache_read_input_tokens": 0}},
            {"usage": {"input_tokens": 1200, "cache_read_input_tokens": 1024}},
            {"usage": {}},
        ]
    )
    cache = result["usage"]["cache_read_input_tokens"]
    assert cache == {
        "reported_sum": 1024,
        "reported_requests": 2,
        "planned_requests": 3,
        "complete": False,
    }
    assert result["usage"]["cache_creation_input_tokens"]["reported_sum"] is None


@pytest.mark.parametrize("status", [200, 503])
async def test_one_post_uses_exact_payload_and_never_retries(monkeypatch, status):
    requests = []
    payload = {"model": "synthetic", "input": [{"role": "user", "content": "离线"}]}

    async def handle(request):
        requests.append(request)
        return httpx.Response(
            status,
            json={
                "status": "completed",
                "output_text": driver.MARKER,
                "usage": {
                    "input_tokens": 1500,
                    "output_tokens": 3,
                    "input_tokens_details": {"cached_tokens": 0},
                },
            },
        )

    original = httpx.AsyncClient

    class OfflineClient(original):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, transport=httpx.MockTransport(handle), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", OfflineClient)
    result = await driver.send_once(
        payload, Settings(auth="none", base_url="http://offline.invalid/v1")
    )
    assert len(requests) == result["http_attempts"] == 1
    assert requests[0].content == driver.encoded(payload)
    assert result["usage"]["cache_read_input_tokens"] == 0
    assert result["raw_usage"]["input_tokens_details"]["cached_tokens"] == 0
    assert result.get("format_passed", False) is (status == 200)
    assert result["status"] == ("completed" if status == 200 else "failed")


async def test_tool_calls_fail_marker_check_and_are_not_executed(monkeypatch):
    async def handle(_request):
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "output_text": driver.MARKER,
                "output": [
                    {
                        "type": "function_call",
                        "call_id": "unexpected",
                        "name": "write_file",
                        "arguments": "{}",
                    }
                ],
            },
        )

    original = httpx.AsyncClient

    class OfflineClient(original):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, transport=httpx.MockTransport(handle), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", OfflineClient)
    result = await driver.send_once({}, Settings(auth="none", base_url="http://offline.invalid/v1"))
    assert result["status"] == "completed" and result["tool_call_count"] == 1
    assert not result["format_passed"]
    assert "cache_read_input_tokens" not in result["usage"]


def test_pair_validation_rejects_unrelated_configuration_or_round_changes(tmp_path, monkeypatch):
    old, new = request_pair()
    monkeypatch.setattr(driver, "HERE", tmp_path)
    for name, rows in (("old", old), ("new", new)):
        driver.write_json(
            tmp_path / name / "manifest.json",
            {
                "configuration": {"stream": False},
                "connection_sha256": "same",
                "experiment_files": {},
                "task_sha256": "same",
                "requests": [{"control": driver.control_record(**row)} for row in rows],
            },
        )
    driver.validate_pair()
    path = tmp_path / "new/manifest.json"
    manifest = json.loads(path.read_text())
    manifest["configuration"]["stream"] = True
    driver.write_json(path, manifest)
    with pytest.raises(ValueError, match="configuration"):
        driver.validate_pair()
    manifest["configuration"]["stream"] = False
    manifest["requests"][1]["control"]["workflow"]["remaining_steps"] = 0
    driver.write_json(path, manifest)
    with pytest.raises(ValueError, match="contents differ"):
        driver.validate_pair()


def test_source_drift_is_rejected_against_real_commit_before_import(tmp_path):
    (tmp_path / "src/aster").mkdir(parents=True)
    (tmp_path / "src/aster/runtime.py").write_text("# synthetic wrong source\n")
    with pytest.raises(ValueError, match="does not exactly match"):
        driver.frozen_imports(tmp_path, driver.OLD_COMMIT)


async def test_request_timeout_consumes_one_attempt_without_retry(monkeypatch):
    calls = []

    async def handle(request):
        calls.append(request)
        await asyncio.sleep(1)
        raise AssertionError("timeout must cancel this request")

    original = httpx.AsyncClient

    class OfflineClient(original):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, transport=httpx.MockTransport(handle), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", OfflineClient)
    monkeypatch.setattr(driver, "REQUEST_SECONDS", 0.01)
    result = await driver.send_once({}, Settings(auth="none", base_url="http://offline.invalid/v1"))
    assert len(calls) == result["http_attempts"] == 1
    assert result["status"] == "failed" and result["error"] == "TimeoutError"


async def test_existing_result_forbids_repeat_before_any_request(tmp_path, monkeypatch):
    settings = Settings(auth="none")
    manifest = {
        "source_commit": "synthetic",
        "source": {},
        "configuration": settings.identity(),
        "connection_sha256": driver.connection_hash(settings),
        "experiment_files": {},
    }
    monkeypatch.setattr(driver, "HERE", tmp_path)
    monkeypatch.setattr(driver, "validate_pair", lambda: [manifest, manifest])
    monkeypatch.setattr(driver, "settings_for", lambda _: settings)
    monkeypatch.setattr(driver, "experiment_snapshot", lambda: {})
    driver.write_json(tmp_path / "old/result.json", {"requests": [{"status": "started"}]})
    args = SimpleNamespace(condition="old", source_commit="synthetic", env_file=None)
    with pytest.raises(ValueError, match="additional attempt"):
        await driver.run(args, {})
