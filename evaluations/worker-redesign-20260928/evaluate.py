"""Three-arm Worker regression/holdout runner, reusing the frozen 20260928 scorer."""
# ruff: noqa: E402
from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.util
import itertools
import json
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
OLD = HERE.parent / "worker-code-20260928"
DATA = REPO / "datasets/worker-code-v3-holdout"
OUT = REPO / "artifacts/private/worker-redesign-20260928"
CACHE = OUT / "objects"
ARMS = ("solo", "handoff_only", "redesigned")

# Import the requested package before loading the historical harness, which
# prepends its own checkout to sys.path. Aster's package __path__ must stay here.
early = argparse.ArgumentParser(add_help=False)
early.add_argument("--source-root", type=Path, default=REPO)
early.add_argument("--source-commit")
source_args, _ = early.parse_known_args()
SOURCE_ROOT = source_args.source_root.resolve()
sys.path.insert(0, str(SOURCE_ROOT / "src"))
import aster

spec = importlib.util.spec_from_file_location("previous_worker_evaluation", OLD / "evaluate.py")
legacy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(legacy)
sys.path.insert(0, str(SOURCE_ROOT / "src"))
from aster.schema import TaskSpec

save = legacy.save


def sha(data):
    return hashlib.sha256(data).hexdigest()


def bank():
    tasks = legacy.bank()
    for task in tasks:
        task["evaluation_split"] = "regression"
    manifest = json.loads((DATA / "manifest.json").read_text())
    for filename, digest in manifest["file_sha256"].items():
        if sha((DATA / filename).read_bytes()) != digest:
            raise ValueError("changed_frozen_holdout: " + filename)
    holdout = [json.loads(line) for line in (DATA / "instances.jsonl").read_text().splitlines()]
    if len(holdout) != 6 or Counter(t["difficulty"] for t in holdout) != {"simple": 2, "medium": 3, "hard": 1}:
        raise ValueError("holdout_distribution_changed")
    prior_ids = {(t["source"], t["upstream_id"]) for t in tasks}
    for task in holdout:
        if (task["source"], task["upstream_id"]) in prior_ids or sha(task["prompt"].encode()) != task["prompt_sha256"]:
            raise ValueError("duplicate_or_changed_holdout")
        task["evaluation_split"] = "holdout"
    if len({t["id"] for t in tasks + holdout}) != 26:
        raise ValueError("duplicate_task_id")
    return tasks + holdout


def check_cache():
    for dataset in (REPO / "datasets/worker-code-v2", DATA):
        lock = json.loads((dataset / "sources.json").read_text())
        for digest, info in (lock["assets"] | lock.get("derived_assets", {})).items():
            data = (CACHE / digest).read_bytes()
            if sha(data) != digest or len(data) != info["bytes"]:
                raise ValueError("cache_checksum_mismatch: " + digest)


def loaded_source():
    if sys.version_info[:2] != (3, 12) or "aster" not in sys.prefix:
        raise RuntimeError("use_conda_aster_or_derived_python312")
    if Path(aster.__file__).resolve() != SOURCE_ROOT / "src/aster/__init__.py":
        raise RuntimeError("wrong_aster_import")
    for name, module in list(sys.modules.items()):
        location = getattr(module, "__file__", None)
        if name.startswith("aster.") and location and not Path(location).resolve().is_relative_to(SOURCE_ROOT / "src/aster"):
            raise RuntimeError("mixed_aster_sources: " + name)
    if not source_args.source_commit:
        raise ValueError("episode_requires_source_commit")
    if subprocess.check_output(["git", "diff", source_args.source_commit, "--", "src/aster"], cwd=SOURCE_ROOT):
        raise RuntimeError("source_differs_from_frozen_commit")
    if subprocess.check_output(["git", "ls-files", "--others", "--exclude-standard", "src/aster"], cwd=SOURCE_ROOT):
        raise RuntimeError("untracked_product_source")
    return legacy.ledger_driver.hashes(SOURCE_ROOT / "src/aster", "**/*.py")


def driver_hashes():
    files = [*HERE.glob("*.py"), HERE / "PROTOCOL.md", *OLD.glob("*.py"),
             Path(legacy.ledger_driver.__file__), *DATA.glob("*.json*"),
             REPO / "src/aster/safety/linux.py",
             REPO / "datasets/worker-code-v2/manifest.json",
             REPO / "datasets/worker-code-v2/instances.jsonl"]
    snapshots = OUT / "driver-snapshots"
    snapshots.mkdir(parents=True, exist_ok=True)
    result = {}
    for path in files:
        content = path.read_bytes()
        digest = sha(content)
        (snapshots / digest).write_bytes(content)
        result[str(path.relative_to(REPO))] = digest
    return result


# Extend only the controller's record lookup; tests, parsers, environment and
# test-integrity restoration are the unchanged existing repository scorer.
import control_adapter
import repository_adapter

original_row = repository_adapter._row
original_runtime = repository_adapter._runtime
original_parse = repository_adapter._parse
original_content = control_adapter.content


def repository_row(task, cache=None):
    digest = task["grading"].get("record_sha256")
    if not digest:
        return original_row(task, cache)
    raw = ((cache or CACHE) / digest).read_bytes()
    if sha(raw) != digest:
        raise ValueError("holdout_record_checksum_mismatch")
    row = json.loads(raw)
    for field, key in (("patch", "reference_sha256"), ("test_patch", "test_patch_sha256"), ("eval_script", "eval_script_sha256")):
        if sha(row[field].encode()) != task["grading"][key]:
            raise ValueError("holdout_row_field_mismatch: " + field)
    if row["instance_id"] != task["upstream_id"]:
        raise ValueError("wrong_repository_record")
    return row


repository_adapter._row = repository_row


def repository_runtime(task):
    if task["upstream_id"] == "django__django-13212":
        root = Path("/tmp/aster-worker-holdout-py39-20260928")
        environment = json.loads((DATA / "environment.json").read_text())
        if sha((root / "lib/python3.9/urllib/parse.py").read_bytes()) != environment["sha256"]:
            raise ValueError("changed_historical_urllib_dependency")
        return root
    return original_runtime(task)


def repository_parse(log, row):
    statuses = original_parse(log, row)
    if row["instance_id"] == "django__django-13212":
        # unittest subTest failures can concatenate parent progress lines.
        # Keep full parent IDs from the original failure summary, not just names.
        starts = list(re.finditer(r"(test\S+ \([^\n)]+\)) \.\.\. ", log))
        for index, match in enumerate(starts):
            end = starts[index + 1].start() if index + 1 < len(starts) else len(log)
            if re.match(r"(?:ok|OK)(?:\s|$)", log[match.end():end]):
                statuses[match[1]] = "PASSED"
        for kind, test_id in re.findall(r"^(ERROR|FAIL): (test\S+ \([^\n)]+\))", log, re.MULTILINE):
            statuses[test_id] = "ERROR" if kind == "ERROR" else "FAILED"
    return statuses


def control_content(task, cache):
    data = original_content(task, cache)
    if task["upstream_id"] == "BigCodeBench/30":
        # Paths only: preserve every upstream test assertion inside Landlock.
        data["tests"] = data["tests"].replace("'/tmp/test_data.json'", "'test_data.json'")
        data["tests"] = data["tests"].replace("'/tmp/non_existent.json'", "'non_existent.json'")
    return data


repository_adapter._runtime = repository_runtime
repository_adapter._parse = repository_parse
control_adapter.content = control_content


def preflight(tasks):
    for task in tasks:
        destination = OUT / "preflight" / task["id"] / "preflight.json"
        if destination.exists():
            print("existing preflight", task["id"], flush=True)
            continue
        prior = REPO / "artifacts/private/worker-code-20260928/preflight" / task["id"] / "preflight.json"
        if task["evaluation_split"] == "regression" and prior.exists():
            result = json.loads(prior.read_text())
            result["reused_gate"] = {"path": str(prior.relative_to(REPO)), "sha256": sha(prior.read_bytes()),
                                     "reason": "unchanged task, independent scorer and prepared dependencies"}
            save(destination, result)
            print("reused preflight", task["id"], result.get("ready"), flush=True)
        else:
            legacy.preflight([task])


async def episode(task, arm):
    bindings = json.loads((OUT / "source-bindings.json").read_text())
    if Path(bindings[arm]["root"]).resolve() != SOURCE_ROOT or bindings[arm]["commit"] != source_args.source_commit:
        raise ValueError("episode_source_differs_from_frozen_binding")
    legacy.SOURCE = source_args.source_commit
    legacy.loaded_source = loaded_source
    legacy.driver_hashes = driver_hashes
    legacy.settings = lambda _: legacy.ledger_driver.config(
        max_workers=0 if arm == "solo" else 2,
        memory_enabled=False, memory_evolution=False, auto_dream=False)

    def task_spec(**kwargs):
        if arm == "redesigned":
            if "max_tokens" not in TaskSpec.model_fields:
                raise RuntimeError("redesigned_source_missing_team_token_budget")
            kwargs["max_tokens"] = legacy.LIMITS[task["difficulty"]]["reported_tokens"]
        return TaskSpec(**kwargs)

    legacy.TaskSpec = task_spec
    loaded_source()
    await legacy.episode(task, arm)
    output = OUT / "runs" / (task["id"] + "--" + arm)
    launch = json.loads((output / "launch.json").read_text())
    launch.update(source_root=str(SOURCE_ROOT), evaluation_split=task["evaluation_split"],
                  internal_team_token_budget=arm == "redesigned")
    save(output / "launch.json", launch)
    result = json.loads((output / "result.json").read_text())
    result.update(evaluation_split=task["evaluation_split"], source_commit=source_args.source_commit)
    save(output / "result.json", result)


def run_tasks(tasks, sources_path):
    sources = json.loads(sources_path.read_text())
    if set(sources) != set(ARMS):
        raise ValueError("sources_require_all_three_arms")
    frozen = OUT / "source-bindings.json"
    if frozen.exists() and json.loads(frozen.read_text()) != sources:
        raise ValueError("source_bindings_already_frozen; use a new evaluation directory for a new iteration")
    for arm, binding in sources.items():
        subprocess.run([sys.executable, str(__file__), "audit", "--source-root", binding["root"],
                        "--source-commit", binding["commit"], "--arm", arm], check=True,
                       cwd=REPO, env=dict(os.environ, PYTHONPATH=str(Path(binding["root"]) / "src")))
    save(frozen, sources)
    rotations = list(itertools.permutations(ARMS))
    indices = {task["id"]: index for index, task in enumerate(bank())}
    for task in tasks:
        gate = OUT / "preflight" / task["id"] / "preflight.json"
        if not gate.exists() or not json.loads(gate.read_text()).get("ready"):
            print("environment_unavailable", task["id"], flush=True)
            continue
        for arm in rotations[indices[task["id"]] % len(rotations)]:
            name = task["id"] + "--" + arm
            output = OUT / "runs" / name
            if (output / "result.json").exists():
                print("existing result", name, flush=True)
                continue
            if output.exists():
                raise RuntimeError("incomplete_episode_requires_audit: " + name)
            binding = sources[arm]
            source_root = Path(binding["root"]).resolve()
            log = OUT / "logs" / (name + ".txt")
            log.parent.mkdir(parents=True, exist_ok=True)
            with log.open("w") as stream:
                process = subprocess.run([sys.executable, str(__file__), "episode", "--task", task["id"],
                    "--arm", arm, "--source-root", str(source_root), "--source-commit", binding["commit"]],
                    cwd=REPO, env=dict(os.environ, PYTHONPATH=str(source_root / "src")),
                    stdout=stream, stderr=subprocess.STDOUT)
            print("episode", name, "exit", process.returncode, flush=True)
            if process.returncode:
                raise RuntimeError("episode_driver_failed; inspect " + str(log))


def main():
    parser = argparse.ArgumentParser(description=__doc__, parents=[early])
    parser.add_argument("mode", choices=("audit", "preflight", "run", "episode"))
    parser.add_argument("--task", action="append")
    parser.add_argument("--split", choices=("regression", "holdout"))
    parser.add_argument("--arm", choices=ARMS)
    parser.add_argument("--sources", type=Path)
    args = parser.parse_args()
    tasks = [task for task in bank() if (not args.task or task["id"] in args.task)
             and (not args.split or task["evaluation_split"] == args.split)]
    if not tasks:
        raise ValueError("no_selected_tasks")
    check_cache()
    if args.mode == "audit":
        if args.source_commit:
            loaded_source()
        if args.arm == "redesigned" and "max_tokens" not in TaskSpec.model_fields:
            raise RuntimeError("redesigned_source_missing_team_token_budget")
        print(json.dumps({"tasks": len(tasks), "splits": dict(Counter(t["evaluation_split"] for t in tasks)),
                          "difficulty": dict(Counter(t["difficulty"] for t in tasks)),
                          "aster_import": aster.__file__, "python": sys.executable}, indent=2))
    elif args.mode == "preflight":
        preflight(tasks)
    elif args.mode == "run":
        if not args.sources:
            raise ValueError("run_requires_frozen_source_bindings")
        run_tasks(tasks, args.sources)
    else:
        if len(tasks) != 1 or not args.arm:
            raise ValueError("episode_requires_one_task_and_arm")
        asyncio.run(episode(tasks[0], args.arm))


legacy.OUT, legacy.CACHE = OUT, CACHE

if __name__ == "__main__":
    main()
