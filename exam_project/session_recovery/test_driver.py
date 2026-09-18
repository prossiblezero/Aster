"""Offline framework checks; synthetic fixtures never become the real candidate."""

import ast
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import benchmark_support
import httpx
import pytest

from aster.config import Settings
from aster.runtime import Runtime
from aster.schema import TaskSpec

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("session_recovery_driver", HERE / "run.py")
driver = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(driver)


def test_frozen_imports_reject_wrong_digest():
    with pytest.raises(ValueError, match="source hash differs"):
        driver.shared.frozen_imports(driver.SOURCE, "wrong")
    digest = driver.shared.source_snapshot(driver.SOURCE)["sha256"]
    support, _ = driver.shared.frozen_imports(driver.SOURCE, digest)
    assert support is benchmark_support


async def test_empty_negative_executes_all_tests_without_changing_candidate(tmp_path):
    previous = benchmark_support.DATASETS
    result = await driver.grade(benchmark_support, tmp_path, 1)
    assert result["tests"]["expected"] == result["tests"]["executed"] == 8
    assert not result["passed"] and result["tests"]["passed"] < 8
    assert result["isolation"] == "landlock+seccomp"
    assert benchmark_support.DATASETS == previous
    assert not list(tmp_path.iterdir())


def test_followup_retains_original_acceptance_and_adds_four_checks():
    def tests(stage):
        return {
            node.name: ast.dump(node)
            for node in ast.parse((HERE / f"checks_stage{stage}.py").read_text()).body
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
        }

    first, second = tests(1), tests(2)
    assert len(first) == 8 and len(second) == 12
    assert all(second[name] == body for name, body in first.items())


def test_shared_budget_counts_injected_turn_and_elapsed_setup():
    state = SimpleNamespace(step=3, turn=4)
    limits, seconds = driver.remaining_budget(state, 9.5)
    assert limits == {"max_steps": 27, "max_turns": 12} and seconds == 1790.5
    for state, elapsed in [
        (SimpleNamespace(step=30, turn=4), 1),
        (SimpleNamespace(step=3, turn=16), 1),
        (SimpleNamespace(step=3, turn=4), 1800),
    ]:
        with pytest.raises(ValueError, match="budget exhausted"):
            driver.remaining_budget(state, elapsed)


def install_offline_transport(monkeypatch):
    payloads = []

    async def handle(request):
        payloads.append(json.loads(request.content))
        if len(payloads) == 1:
            output = [
                {
                    "type": "function_call",
                    "call_id": "synthetic-write",
                    "name": "write_file",
                    "arguments": json.dumps(
                        {
                            "path": "synthetic.txt",
                            "content": "Synthetic offline fixture.\n",
                        }
                    ),
                }
            ]
        else:
            assert "synthetic-write" in json.dumps(payloads[-1])
            output = [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [
                        {"type": "output_text", "text": "Synthetic recovery completed."},
                    ],
                }
            ]
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "output": output,
                "usage": {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
            },
        )

    original = httpx.AsyncClient

    class OfflineClient(original):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handle)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", OfflineClient)
    return payloads


def offline_settings():
    return Settings(
        auth="none",
        model="synthetic",
        base_url="http://offline.invalid/v1",
        context_tokens=128000,
        output_tokens=1024,
        sandbox="required",
        network=False,
        approval="auto",
        completion="off",
        memory_enabled=False,
        memory_evolution=False,
        auto_dream=False,
        max_workers=0,
    )


async def test_actual_restore_preserves_successful_write_and_excludes_synthetic_http(
    tmp_path, monkeypatch
):
    payloads = install_offline_transport(monkeypatch)
    saved = {}
    runtime, gate, result = await driver.stage_one(
        tmp_path,
        offline_settings(),
        TaskSpec(prompt="Create synthetic offline fixture", max_steps=30, max_turns=16),
        lambda name, value: saved.update({name: json.loads(json.dumps(value))}),
    )
    assert result["status"] == "completed" and result["budget_respected"]
    assert result["before_fault"]["stop_reason"] == "provider_error"
    assert result["before_fault"]["turns"] == 2 and result["total_turns"] == 3
    assert result["recovery_budget"]["max_turns"] == 14
    assert result["recovery_budget"]["max_steps"] == 29
    assert result["recovery_budget"]["seconds"] < driver.WALL_SECONDS
    assert all(result["restore_checks"].values())
    assert result["first_write_executed_once"] and result["prior_success_ids_retained"]
    assert gate.injection["before_http"] and gate.injection["http_attempts"] == 0
    assert len(payloads) == 2 and len(gate.writes) == 1
    assert runtime.state.used_call_ids == ["synthetic-write"]
    assert (tmp_path / "synthetic.txt").read_text() == "Synthetic offline fixture.\n"
    total = driver.totals([result["before_fault"], result["after_resume"]], benchmark_support)
    assert total["real_model_requests"] == total["http_attempts"] == 2
    assert total["http_retries"] == total["real_request_errors"] == 0
    assert "stage1-before-resume.json" in saved and "stage1-runtime.json" in saved


async def test_failed_resume_preserves_consumed_usage_without_new_attempt(tmp_path, monkeypatch):
    payloads = install_offline_transport(monkeypatch)

    def broken_resume(*args, **kwargs):
        raise ValueError("Synthetic restoration rejection")

    monkeypatch.setattr(Runtime, "resume", broken_resume)
    runtime, gate, result = await driver.stage_one(
        tmp_path,
        offline_settings(),
        TaskSpec(prompt="Create synthetic offline fixture"),
        lambda *_: None,
    )
    assert result["status"] == "recovery_setup_failed"
    assert result["before_fault"]["usage"]["input_tokens"] == 10
    assert result["total_turns"] == 2 and len(payloads) == 1
    assert runtime.state.run_id == gate.injection["run_id"]
    assert result["prior_success_ids_retained"] and result["first_write_executed_once"]


async def test_existing_attempt_cannot_be_repeated(tmp_path, monkeypatch):
    monkeypatch.setattr(driver, "HERE", tmp_path)
    (tmp_path / "run").mkdir()
    with pytest.raises(ValueError, match="Refusing to repeat"):
        await driver.run_experiment(SimpleNamespace(), benchmark_support, {})


def test_credential_scan_reports_paths_without_disclosing_secret(tmp_path):
    (tmp_path / "synthetic.log").write_text("synthetic-test-secret")
    result = driver.credential_scan(tmp_path, "synthetic-test-secret")
    assert result == {"files_scanned": 1, "plaintext_found": True, "paths": ["synthetic.log"]}
    assert "synthetic-test-secret" not in json.dumps(result)
