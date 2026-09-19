"""Offline checks: source pinning, evidence privacy, replies and cancellation."""
import importlib.util
import json
import shutil
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("lab_pair_driver", Path(__file__).with_name("run_pair.py"))
driver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(driver)


@pytest.fixture(scope="module")
def source_snapshot(tmp_path_factory):
    source = tmp_path_factory.mktemp("aster-source-snapshot")
    shutil.copytree(driver.ROOT / "src", source / "src",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copy2(driver.ROOT / "pyproject.toml", source / "pyproject.toml")
    return source


def test_frozen_imports_match_manifest_and_have_no_inherited_credentials(tmp_path, monkeypatch, source_snapshot):
    source = source_snapshot
    monkeypatch.setenv("ASTER_API_KEY", "must-not-inherit")
    env = driver.child_environment(source.resolve())
    assert "ASTER_API_KEY" not in env
    assert env["PYTHONDONTWRITEBYTECODE"] == "1"
    frozen = driver.manifest(source.resolve())
    imports = driver.capture_json(driver.IMPORT_PROBE, [str(source.resolve())], tmp_path, env, driver.Redactor())
    for module in imports["modules"].values():
        path = Path(module["path"])
        assert module["sha256"] == frozen["files"][str(path.relative_to(source.resolve()))]
    assert not list(tmp_path.iterdir())


def test_redaction_and_bounded_operator_answers(tmp_path):
    credentials = tmp_path / "credentials.env"
    credentials.write_text("CUSTOM_API_KEY='synthetic-secret-value'\n")
    config = tmp_path / "settings.toml"
    config.write_text('[profiles.main]\nbase_url="https://private-gateway.invalid/v1"\n')
    redactor = driver.Redactor(credentials, config)
    result = redactor.value({"error": "synthetic-secret-value at private-gateway.invalid",
                             "base_url": "https://private-gateway.invalid/v1/responses"})
    assert "synthetic-secret-value" not in json.dumps(result)
    assert "private-gateway.invalid" not in json.dumps(result)
    event = {"event": "permission_decision", "decision": "ask", "call": {"name": "ask_user"}}
    assert driver.responses(event, 0) == ([("approval", "y"), ("clarification", driver.ANSWER)], 1, False)
    assert driver.responses(event, 2) == ([], 3, True)
    assert driver.responses({**event, "decision": "deny"}, 0) == ([], 0, False)


def test_cancelled_child_still_exports_final_checkpoint(tmp_path):
    workspace, result = tmp_path / "workspace", tmp_path / "result"
    workspace.mkdir()
    result.mkdir()
    program = r'''
import json, pathlib, signal, sys, time
root = pathlib.Path(".aster/runs/offline")
root.mkdir(parents=True)
(root / "trace.jsonl").write_text(json.dumps({"event": "provider_attempt", "usage": None}) + "\n")
def stop(*_):
    (root / "checkpoint.json").write_text(json.dumps({"state": {
        "run_id": "offline", "phase": "stopped", "stop_reason": "cancelled", "usage": {}}}))
    raise SystemExit(130)
signal.signal(signal.SIGINT, stop)
print("offline ready", flush=True)
while True:
    time.sleep(0.1)
'''
    summary = driver.drive([str(driver.PYTHON), "-c", program], workspace,
                           driver.child_environment(driver.ROOT), result, 0.5, driver.Redactor())
    assert summary["timed_out"] and summary["exit_code"] == 130
    assert "offline ready" in (result / "stdout.txt").read_text()
    runs = driver.collect_runs(workspace, result, driver.Redactor())
    assert runs[0]["stop_reason"] == "cancelled"
    assert '"usage": null' in (result / "offline-events.jsonl").read_text()
    assert (result / "offline-state.json").exists()
    assert driver.collect_runs(workspace, result, driver.Redactor(), {"offline"}) == []


def test_boundary_probe_denies_external_file_and_preserves_product_workspace(tmp_path, source_snapshot):
    sentinel = tmp_path / "external.txt"
    sentinel.write_text("external")
    source = source_snapshot
    probe = driver.capture_json(driver.ISOLATION_PROBE, [
        json.dumps([{"path": str(sentinel), "directory": False}])
    ], tmp_path, driver.child_environment(source), driver.Redactor())
    assert probe["passed"], probe
    assert sentinel.read_text() == "external"
    assert list(tmp_path.iterdir()) == [sentinel]
