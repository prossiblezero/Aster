"""Offline validation for datasets/worker-code-v1.

This validates the checked-in selection and schema only. It intentionally does
not download repositories, execute patches, or run the SWE-bench harness.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "manifest.json"
INSTANCES = ROOT / "instances.jsonl"


def fail(message: str) -> None:
    raise SystemExit(f"worker-code-v1 validation failed: {message}")


def main() -> None:
    manifest = json.loads(MANIFEST.read_text())
    lines = [line for line in INSTANCES.read_text().splitlines() if line.strip()]
    records = [json.loads(line) for line in lines]
    if len(records) != manifest["selection"]["total"]:
        fail("manifest total does not match instances.jsonl")
    ids = [record["id"] for record in records]
    if len(ids) != len(set(ids)):
        fail("duplicate instance id")
    counts = {level: sum(r["difficulty"] == level for r in records) for level in ("simple", "medium", "hard")}
    if counts != {"simple": 6, "medium": 10, "hard": 4}:
        fail(f"unexpected difficulty counts: {counts}")
    expected_hash = manifest["selection"]["selection_sha256"]
    actual_hash = hashlib.sha256((INSTANCES.read_text()).encode()).hexdigest()
    if actual_hash != expected_hash:
        fail("selection_sha256 does not match instances.jsonl")

    for record in records:
        required = {"id", "suite", "split", "difficulty", "prompt", "source", "grader", "harness", "decomposition"}
        missing = required - record.keys()
        if missing:
            fail(f"{record['id']} missing fields: {sorted(missing)}")
        if record["suite"] != "worker_repository" or record["split"] != "evaluation":
            fail(f"{record['id']} has invalid suite/split")
        source = record["source"]
        for field in ("instance_id", "repo", "base_commit", "environment_setup_commit", "problem_sha256"):
            if not source.get(field):
                fail(f"{record['id']} missing source.{field}")
        grader = record["grader"]
        if grader.get("model_visible") is not False:
            fail(f"{record['id']} grader is not explicitly hidden")
        for field in ("gold_patch_sha256", "test_patch_sha256", "eval_script_sha256"):
            value = grader.get(field, "")
            if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
                fail(f"{record['id']} has invalid {field}")
        decomposition = record["decomposition"]
        units = decomposition.get("units", [])
        if not units:
            fail(f"{record['id']} has no decomposition unit")
        if record["difficulty"] == "simple":
            if decomposition["parallelizable"] or decomposition["expected_workers"] != 0:
                fail(f"{record['id']} simple control is marked parallel")
        else:
            if not decomposition["parallelizable"] or decomposition["expected_workers"] < 2 or len(units) < 2:
                fail(f"{record['id']} coordination candidate lacks independent units")
        for unit in units:
            for field in ("unit_id", "goal", "path_globs", "evidence_paths", "dependencies", "scope", "allowed_write_paths", "output_artifact", "acceptance_refs"):
                if field not in unit:
                    fail(f"{record['id']} unit missing {field}")
            if not unit["path_globs"] or not unit["evidence_paths"]:
                fail(f"{record['id']} unit has empty paths")
            if unit["scope"] != "read_only" or unit["allowed_write_paths"]:
                fail(f"{record['id']} unit is allowed to write shared workspace")
        if record["difficulty"] != "simple" and not decomposition["integration_paths"]:
            fail(f"{record['id']} lacks integration path")
        if "gold_patch" in record["prompt"] or "test_patch" in record["prompt"]:
            fail(f"{record['id']} prompt mentions hidden patch field")

    print(f"ok: {len(records)} instances, counts={counts}, selection_sha256={actual_hash}")


if __name__ == "__main__":
    main()
