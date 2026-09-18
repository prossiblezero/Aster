"""Offline admission, frozen-source fairness and deterministic answer grading."""

import importlib.util
import json
import shutil
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "memory_probe", Path(__file__).with_name("memory_probe.py")
)
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


async def test_prepare_uses_runtime_evidence_and_keeps_source_and_seed_stable(tmp_path):
    source = tmp_path / "product"
    source.mkdir()
    (source / "index.html").write_text("<!doctype html><title>Offline fixture</title>")
    shutil.copytree(probe.HERE / "rules", source / "docs")
    before = probe.snapshot(source)
    target = tmp_path / "memory"
    manifest = await probe.prepare(source, target, "offline-test-source")
    assert probe.snapshot(source) == before == manifest["seed_product"]
    assert len(manifest["records"]) == 4
    for record in manifest["records"]:
        stored = record["store"]["data"]
        assert stored["text"] == record["text"] and stored["tags"] == []
        assert stored["provenance"]["evidence_id"] == record["read"]["evidence_id"]
        assert stored["sources"] == {record["path"]: record["read"]["data"]["source_sha256"]}
    diagnostics = json.loads((target / "diagnostics.json").read_text())
    assert all(row["expected_recalled"] for row in diagnostics["queries"])
    assert diagnostics["unrelated_ids"] == []
    assert diagnostics["changed_source_old_lesson_absent"]
    assert diagnostics["seed_sources_unchanged"]
    assert probe.snapshot(target / "seed", include_state=True) == manifest["seed_all_files"]
    assert [(case["question"], case["memory"]) for case in manifest["cases"]] == probe.ORDER
    for case in manifest["cases"]:
        command = case["command"]
        assert ("--memory" in command) == case["memory"]
        for key, value in [("--steps", "8"), ("--turns", "6"), ("--seconds", "360")]:
            assert command[command.index(key) + 1] == value
    with pytest.raises(ValueError, match="overwrite"):
        await probe.prepare(source, target, "offline-test-source")


@pytest.mark.parametrize("question", probe.ANSWERS)
def test_grade_requires_exact_facts_and_source_coverage(question):
    records = [
        {"path": f"docs/{name}", "read": {"evidence_id": str(index)}}
        for index, name in enumerate(probe.RULES)
    ]
    answer = {**probe.ANSWERS[question], "sources": sorted(probe.SOURCES[question])}
    assert probe.grade(question, json.dumps(answer), records)["passed"]
    answer["sources"] = ["not-an-actual-source.md"]
    assert not probe.grade(question, json.dumps(answer), records)["passed"]
    answer["sources"] = [f"evidence:{row['read']['evidence_id']}" for row in records]
    assert probe.grade(question, json.dumps(answer), records)["passed"]
    field = next(iter(probe.ANSWERS[question]))
    answer[field] = "wrong"
    assert not probe.grade(question, json.dumps(answer), records)["passed"]
    assert not probe.grade(question, "```json\n" + json.dumps(answer) + "\n```", records)["passed"]


def test_first_request_match_uses_decoded_text_not_json_escaped_excerpt():
    excerpt = '数据包结构为 {"version":1,"tasks":[]}。'
    payload = {"input": [{"content": [{"text": excerpt + " (evidence:abc)"}]}]}
    assert excerpt in "\n".join(probe.text_values(payload))


async def test_prepare_rejects_changed_public_sources_before_writing(tmp_path):
    source = tmp_path / "product"
    source.mkdir()
    (source / "index.html").write_text("<title>Offline fixture</title>")
    shutil.copytree(probe.HERE / "rules", source / "docs")
    (source / "docs/data-contract.md").write_text("changed")
    with pytest.raises(ValueError, match="public rules"):
        await probe.prepare(source, tmp_path / "memory", "offline-test-source")
    assert not (tmp_path / "memory").exists()
