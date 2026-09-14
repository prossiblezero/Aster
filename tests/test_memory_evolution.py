"""Scenario data lives in datasets; tests verify the policy and persistence contracts."""

import json
from pathlib import Path

import pytest
from mechanism_support import memory_case

from aster.memory import Memory
from aster.workspace import digest

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
