"""Scenario data lives in datasets; tests verify the policy and persistence contracts."""

import json
from pathlib import Path

import pytest
from mechanism_support import memory_case

from aster.knowledge.memory import Memory
from aster.storage.workspace import digest

CASES = json.loads(
    (Path(__file__).resolve().parents[1] / "datasets/memory_evolution_cases.json").read_text()
)["cases"]


def record(memory, text, *, evidence_id="observed-1", **kwargs):
    return memory.record(text, run_id="fixture-run", evidence_id=evidence_id, **kwargs)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
@pytest.mark.parametrize("evolution", [False, True], ids=["evolution_off", "evolution_on"])
def test_memory_scenarios(tmp_path, case, evolution):
    row = memory_case(case, tmp_path, evolution=evolution)
    assert row["contract_passed"], row["checks"]


def test_legacy_store_and_discard_never_become_authorization(tmp_path):
    memory = Memory(tmp_path)
    text = "Run repository tests before publishing."
    item = record(memory, text, observation=text)
    assert memory.discard(item["id"])
    assert memory.retrieve("repository") == []
    memory.state["schema"] = 1
    memory.state["lessons"][0].pop("inactive_reason")
    memory.save()
    migrated = Memory(tmp_path)
    assert migrated.retrieve("repository") == []
    assert migrated.state["lessons"][0]["inactive_reason"] == "unverified_legacy"


def test_private_runtime_secret_is_not_persisted(tmp_path):
    memory = Memory(tmp_path, secrets=["private-provider-credential"])
    text = "Endpoint credential: private-provider-credential"
    assert record(memory, text, observation=text) is None
    assert "private-provider-credential" not in memory.path.read_text()


def test_disabled_durable_memory_retains_session_observations(tmp_path):
    memory = Memory(tmp_path, enabled=False)
    working = {}
    memory.remember_file("config.py", "port = 8080", working)
    assert "port = 8080" in memory.working_section(working)
    assert memory.retrieve("port") == []
    assert "working" not in memory.state


def test_restoring_excerpt_does_not_erase_source_dependency(tmp_path):
    memory = Memory(tmp_path)
    text = "Service port is 8080."
    source = tmp_path / "config.txt"
    source.write_text(text)
    item = record(memory, text, observation=text, sources={source.name: digest(text)})
    repeated = record(memory, text, evidence_id="e2", observation=text)
    assert repeated["sources"] == item["sources"]
    source.write_text("Service port is 9090.")
    assert record(memory, text, evidence_id="e3", observation=text) is None
    assert memory.last_rejection == "stale_source"
    assert memory.retrieve("Service port") == []


@pytest.mark.parametrize(
    "enabled,evolution,reason",
    [
        (False, True, "memory_disabled"),
        (True, False, "evolution_disabled"),
        (True, True, "no_active_excerpts"),
    ],
)
def test_dream_reports_skipped_maintenance_without_writing_state(
    tmp_path, enabled, evolution, reason
):
    memory = Memory(tmp_path, enabled=enabled, evolution=evolution)
    assert memory.dream() == {
        "status": "skipped",
        "reason": reason,
        "checked": 0,
        "deactivated": 0,
    }
    assert not memory.path.exists()


def test_dream_reports_checked_and_deactivated_excerpts_without_summarizing(tmp_path):
    memory = Memory(tmp_path)
    text = "Service port is 8080."
    source = tmp_path / "config.txt"
    source.write_text(text)
    item = record(memory, text, observation=text, sources={source.name: digest(text)})
    assert memory.dream() == {
        "status": "completed",
        "reason": "",
        "checked": 1,
        "deactivated": 0,
    }
    source.write_text("Service port is 9090.")
    assert memory.dream() == {
        "status": "completed",
        "reason": "",
        "checked": 1,
        "deactivated": 1,
    }
    saved = memory.state["lessons"]
    assert len(saved) == 1 and saved[0]["id"] == item["id"] and saved[0]["text"] == text
    assert saved[0]["inactive_reason"] == "stale_source"
    assert memory.dream()["reason"] == "no_active_excerpts"
