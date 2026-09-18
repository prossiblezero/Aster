"""Driver and grader checks: no model request and no operator-authored candidate."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import benchmark_support
import httpx
import pytest

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("stream_comparison_driver", HERE / "run.py")
driver = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(driver)


def frozen_source(tmp_path):
    root = tmp_path / "frozen"
    (root / "src/aster").mkdir(parents=True)
    (root / "scripts").mkdir()
    for name in ("src/aster/runtime.py", "src/aster/__init__.py", "scripts/benchmark_support.py"):
        (root / name).write_text("# synthetic source identity fixture, not a candidate\n")
    return root


def test_source_manifest_covers_python_and_rejects_drift_before_import(tmp_path, monkeypatch):
    source = frozen_source(tmp_path)
    snapshot = driver.source_snapshot(source)
    assert len(snapshot["python_files"]) == 3
    (source / "src/aster/runtime.py").write_text("# changed\n")
    with pytest.raises(ValueError, match="source hash differs"):
        driver.frozen_imports(source, snapshot["sha256"])
    monkeypatch.delenv("PYTHONPATH", raising=False)
    with pytest.raises(ValueError, match="Set PYTHONPATH"):
        driver.frozen_imports(source, driver.source_snapshot(source)["sha256"])


async def test_independent_grader_runs_all_checks_and_rejects_empty_project(tmp_path):
    empty = tmp_path / "candidate"
    empty.mkdir()
    previous = benchmark_support.DATASETS
    result = await driver.grade(benchmark_support, empty)
    assert benchmark_support.DATASETS == previous
    assert not result["passed"]
    assert result["tests"]["expected"] == result["tests"]["executed"] == 7
    assert result["tests"]["passed"] < 7
    assert result["isolation"] == "landlock+seccomp"
    assert not list(empty.iterdir())


async def test_grader_exception_is_recorded_and_fixture_root_restored(tmp_path):
    async def broken(root, checks):
        assert checks == "checks.py"
        assert support.DATASETS == HERE
        raise OSError("synthetic grading failure")

    support = SimpleNamespace(DATASETS=tmp_path, external_check=broken)
    result = await driver.grade(support, tmp_path)
    assert result == {"passed": False, "status": "grader_error", "error": "OSError"}
    assert support.DATASETS == tmp_path


async def test_failed_run_and_grader_still_save_state_and_usage(tmp_path, monkeypatch):
    import aster.runtime

    source = frozen_source(tmp_path)
    monkeypatch.setattr(driver, "HERE", tmp_path)
    (tmp_path / "requirements.md").write_text("Synthetic driver test, never sent to a model.")
    (tmp_path / "checks.py").write_text((HERE / "checks.py").read_text())
    env = tmp_path / ".env"
    env.write_text("")
    state = SimpleNamespace(
        run_id="synthetic",
        phase="failed",
        stop_reason="synthetic_error",
        step=0,
        turn=1,
        usage={"input_tokens": 42},
    )

    class FailedRuntime:
        def __init__(self, *_args, **_kwargs):
            self.state = state

        async def run(self, task):
            raise RuntimeError("synthetic runtime failure")

    count = 0

    async def checks(root, verifier):
        nonlocal count
        count += 1
        if count == 1:
            return {"passed": False, "tests": {"executed": 7}}
        raise OSError("synthetic grader failure")

    support = SimpleNamespace(
        DATASETS=tmp_path,
        external_check=checks,
        usage_metrics=lambda usage, requests: {"reported_input_tokens": usage["input_tokens"]},
    )
    monkeypatch.setattr(aster.runtime, "Runtime", FailedRuntime)
    monkeypatch.setattr(
        driver, "timed_model_class", lambda: lambda settings: SimpleNamespace(requests=[])
    )
    args = SimpleNamespace(
        condition="nonstream", env_file=env, source_commit="synthetic", source_path=source
    )
    assert await driver.run_condition(args, support, driver.source_snapshot(source)) == 1
    result = json.loads((tmp_path / "nonstream/result.json").read_text())
    runtime_result = json.loads((tmp_path / "nonstream/evidence/runtime-result.json").read_text())
    assert runtime_result["usage"] == result["usage"] == {"input_tokens": 42}
    assert result["error"] == "RuntimeError"
    assert result["independent_grade"]["status"] == "grader_error"
    assert result["source_unchanged"]
    assert not list((tmp_path / "nonstream/candidate").iterdir())


async def test_timing_hooks_count_real_transport_attempts_without_network(monkeypatch):
    from aster.config import Settings

    payloads = []

    async def handle(request):
        payloads.append(json.loads(request.content))
        if len(payloads) == 1:
            return httpx.Response(503, json={"error": {"message": "synthetic retry"}})
        return httpx.Response(
            200,
            json={
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "Synthetic timing result"}],
                    }
                ],
                "usage": {"input_tokens": 10, "output_tokens": 4, "total_tokens": 14},
            },
        )

    original = httpx.AsyncClient

    class OfflineClient(original):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handle)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", OfflineClient)
    model = driver.timed_model_class()(
        Settings(auth="none", model="synthetic", base_url="http://offline.invalid/v1")
    )
    result = await model.complete([{"role": "user", "content": "offline timing fixture"}], [])
    assert result.text == "Synthetic timing result"
    request = model.requests[0]
    assert request["http_attempts"] == 2 and request["retries"] == 1
    assert request["first_event_seconds"] == request["first_text_seconds"]
    assert 0 < request["first_text_seconds"] <= request["total_seconds"]
    assert request["usage"]["total_tokens"] == 14
    assert payloads[0] == payloads[1] and "stream" not in payloads[0]


async def test_stream_timings_and_observer_are_preserved_without_network(monkeypatch):
    from aster.config import Settings

    terminal = {
        "status": "completed",
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "你好"}],
            }
        ],
        "usage": {"input_tokens": 5, "output_tokens": 2},
    }
    frames = [
        {
            "type": "response.output_text.delta",
            "delta": "你好",
            "output_index": 0,
            "item_id": "message",
        },
        {"type": "response.completed", "response": terminal},
    ]
    payloads = []

    async def handle(request):
        payloads.append(json.loads(request.content))
        content = "".join(
            "data: " + json.dumps(value, ensure_ascii=False) + "\n\n" for value in frames
        ).encode()
        return httpx.Response(200, content=content)

    original = httpx.AsyncClient

    class OfflineClient(original):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handle)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", OfflineClient)
    model = driver.timed_model_class()(
        Settings(auth="none", stream=True, model="synthetic", base_url="http://offline.invalid/v1")
    )
    observed = []
    reply = await model.complete([], [], on_event=observed.append)
    assert reply.text == "你好"
    record = model.requests[0]
    assert record["http_attempts"] == 1 and record["retries"] == 0
    assert (
        0
        <= record["first_event_seconds"]
        <= record["first_text_seconds"]
        <= record["total_seconds"]
    )
    assert record["stream_attempts"][0]["type"] == "completed"
    assert [item["type"] for item in observed] == ["begin", "text_delta", "completed"]
    assert payloads[0]["stream"] is True
