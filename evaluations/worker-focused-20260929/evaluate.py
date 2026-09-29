"""Four previously evaluated tasks, two same-source arms, three fixed repeats."""
# ruff: noqa: E402
from __future__ import annotations

import argparse
import asyncio
import fcntl
import importlib.util
import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
OUT = REPO / "artifacts/private/worker-focused-20260929"
DATA = REPO / "datasets/worker-focused-20260929/selection.json"
spec = importlib.util.spec_from_file_location("focused_prior", HERE.parent / "worker-redesign-20260928/evaluate.py")
prior = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prior)
legacy, sha, save = prior.legacy, prior.sha, prior.save
ARMS, REPEATS = ("solo", "worker"), (1, 2, 3)


def bank():
    selection = json.loads(DATA.read_text())
    if selection["repeats"] != 3 or selection["arms"] != list(ARMS):
        raise ValueError("changed_design")
    tasks = []
    for item in selection["tasks"]:
        path = REPO / item["dataset"]
        if sha(path.read_bytes()) != item["dataset_sha256"]:
            raise ValueError("changed_source_dataset")
        task = next(json.loads(line) for line in path.read_text().splitlines() if json.loads(line)["id"] == item["id"])
        digest = sha(json.dumps(task, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode())
        if digest != item["task_sha256"] or sha(task["prompt"].encode()) != item["prompt_sha256"]:
            raise ValueError("changed_source_task")
        tasks.append(task)
    if len(tasks) != 4 or len({t["id"] for t in tasks}) != 4 or Counter(t["difficulty"] for t in tasks) != {"medium": 2, "hard": 2}:
        raise ValueError("changed_selection")
    return tasks


def gates():
    selection = json.loads(DATA.read_text())
    for filename, digest in selection["unchanged_grader_files"].items():
        if sha((REPO / filename).read_bytes()) != digest:
            raise ValueError("reused_gate_grader_changed: " + filename)
    result = {}
    for item in selection["tasks"]:
        path = REPO / item["preflight"]["path"]
        if sha(path.read_bytes()) != item["preflight"]["sha256"]:
            raise ValueError("changed_preflight_gate")
        gate = json.loads(path.read_text())
        if not gate["ready"] or gate["starter"]["passed"] or not gate["reference"]["passed"]:
            raise ValueError("preflight_not_ready")
        if gate["reference"].get("missing_tests") or not Path(gate["prepare"]["test_python"]).is_file():
            raise ValueError("incomplete_preflight_environment")
        gate["focused_reused_gate"] = item["preflight"]
        result[item["id"]] = gate
    return result


def settings(arm):
    return legacy.ledger_driver.config(max_workers=0 if arm == "solo" else 2,
        memory_enabled=False, memory_evolution=False, auto_dream=False)


def driver_hashes():
    paths = {*HERE.glob("*.py"), HERE / "PROTOCOL.md", DATA, Path(prior.__file__),
             *prior.OLD.glob("*.py"), Path(legacy.ledger_driver.__file__), REPO / "src/aster/safety/linux.py"}
    for item in json.loads(DATA.read_text())["tasks"]:
        paths.update((REPO / item["dataset"]).parent.glob("*.json"))
        paths.add(REPO / item["dataset"])
    # Include the existing dataset validator and the exact historical parser.
    paths.update((REPO / "datasets/worker-code-v2/checks").glob("*.py"))
    paths.add(REPO / "artifacts/private/worker-code-20260928/official_log_parsers.py")
    snapshots = OUT / "driver-snapshots"
    snapshots.mkdir(parents=True, exist_ok=True)
    hashes = {}
    for path in sorted(paths):
        content = path.read_bytes()
        digest = sha(content)
        (snapshots / digest).write_bytes(content)
        hashes[str(path.relative_to(REPO))] = digest
    return hashes


def schedule(tasks):
    return [(task, repeat, arm) for index, task in enumerate(tasks) for repeat in REPEATS
            for arm in (ARMS if (index + repeat - 1) % 2 == 0 else ARMS[::-1])]


def binding(path):
    sources = json.loads(path.read_text())
    if set(sources) != set(ARMS) or sources["solo"] != sources["worker"]:
        raise ValueError("both_arms_require_identical_source_bindings")
    return sources


def identity(sources):
    configs = {arm: settings(arm) for arm in ARMS}
    environments = {}
    for executable in {gate["prepare"]["test_python"] for gate in gates().values()}:
        packages = subprocess.check_output([executable, "-c", "import importlib.metadata as m,json; "
            "print(json.dumps(sorted((d.metadata['Name'],d.version) for d in m.distributions())))"])
        environments[executable] = {"python_sha256": sha(Path(executable).read_bytes()), "packages_sha256": sha(packages)}
    return {"sources": sources, "driver_hashes": driver_hashes(),
            "settings": {arm: config.identity() for arm, config in configs.items()},
            "endpoint_sha256": {arm: sha(config.base_url.encode()) for arm, config in configs.items()},
            "environments": environments,
            "gates": {task: gate["focused_reused_gate"] for task, gate in gates().items()}}


async def episode(task, arm, repeat):
    frozen = json.loads((OUT / "run-lock.json").read_text())
    sources = frozen["sources"]
    if frozen != identity(sources):
        raise ValueError("changed_frozen_run_inputs")
    source = sources[arm]
    if Path(source["root"]).resolve() != prior.SOURCE_ROOT or source["commit"] != prior.source_args.source_commit:
        raise ValueError("wrong_episode_source_binding")
    legacy.OUT = OUT / f"repeat-{repeat:02d}"
    legacy.CACHE = prior.CACHE
    save(legacy.OUT / "preflight" / task["id"] / "preflight.json", gates()[task["id"]])
    legacy.SOURCE = source["commit"]
    legacy.loaded_source = prior.loaded_source
    legacy.driver_hashes = driver_hashes
    legacy.settings = settings
    budget = legacy.LIMITS[task["difficulty"]]["reported_tokens"]
    legacy.TaskSpec = lambda **kwargs: prior.TaskSpec(**kwargs, max_tokens=budget)
    prior.loaded_source()
    await legacy.episode(task, arm)
    output = legacy.OUT / "runs" / (task["id"] + "--" + arm)
    for filename in ("launch.json", "result.json"):
        row = json.loads((output / filename).read_text())
        row.update(repeat=repeat, evaluation_split="development_seen", source_root=str(prior.SOURCE_ROOT),
                   source_commit=source["commit"], internal_team_token_budget=budget,
                   preflight_sha256=frozen["gates"][task["id"]]["sha256"])
        save(output / filename, row)
    if frozen != identity(sources):
        raise ValueError("run_inputs_changed_during_episode")


def run(tasks, sources_path):
    sources = binding(sources_path)
    source = sources["solo"]
    subprocess.run([sys.executable, str(__file__), "audit", "--source-root", source["root"],
                    "--source-commit", source["commit"]], check=True, cwd=REPO,
                   env=dict(os.environ, PYTHONPATH=str(Path(source["root"]) / "src")))
    lockfile = OUT / "run-lock.json"
    current = identity(sources)
    if lockfile.exists() and json.loads(lockfile.read_text()) != current:
        raise ValueError("run_already_frozen_with_different_inputs")
    save(lockfile, current)
    save(OUT / "source-bindings.json", sources)
    for task, repeat, arm in schedule(tasks):
        name = task["id"] + "--" + arm
        output = OUT / f"repeat-{repeat:02d}" / "runs" / name
        if (output / "result.json").exists():
            print("existing_result", repeat, name, flush=True)
            continue
        if output.exists():
            raise RuntimeError("incomplete_episode_requires_audit: " + str(output))
        log = OUT / "logs" / f"repeat-{repeat:02d}--{name}.txt"
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("w") as stream:
            result = subprocess.run([sys.executable, str(__file__), "episode", "--task", task["id"],
                "--repeat", str(repeat), "--arm", arm, "--source-root", source["root"],
                "--source-commit", source["commit"]], cwd=REPO,
                env=dict(os.environ, PYTHONPATH=str(Path(source["root"]) / "src")),
                stdout=stream, stderr=subprocess.STDOUT)
        print("episode", repeat, name, "exit", result.returncode, flush=True)
        if result.returncode:
            raise RuntimeError("episode_driver_failed: " + str(log))


def main():
    parser = argparse.ArgumentParser(description=__doc__, parents=[prior.early])
    parser.add_argument("mode", choices=("audit", "run", "episode"))
    parser.add_argument("--sources", type=Path)
    parser.add_argument("--task")
    parser.add_argument("--repeat", type=int, choices=REPEATS)
    parser.add_argument("--arm", choices=ARMS)
    args = parser.parse_args()
    tasks = bank()
    prior.check_cache()
    gates()
    if "max_tokens" not in prior.TaskSpec.model_fields:
        raise RuntimeError("source_missing_shared_token_budget")
    if args.mode == "audit":
        if sys.version_info[:2] != (3, 12) or "aster" not in sys.prefix:
            raise RuntimeError("use_conda_aster_or_derived_python312")
        if Path(prior.aster.__file__).resolve() != prior.SOURCE_ROOT / "src/aster/__init__.py":
            raise RuntimeError("wrong_aster_import")
        if args.source_commit:
            prior.loaded_source()
            head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=prior.SOURCE_ROOT, text=True).strip()
            if head != args.source_commit:
                raise ValueError("source_checkout_head_differs_from_binding")
        configs = {arm: settings(arm).identity() for arm in ARMS}
        assert {k: v for k, v in configs["solo"].items() if k != "max_workers"} == {
            k: v for k, v in configs["worker"].items() if k != "max_workers"}
        assert len(schedule(tasks)) == len(set((t["id"], r, a) for t, r, a in schedule(tasks))) == 24
        print(json.dumps({"tasks": len(tasks), "episodes": 24, "reused_gates": len(gates()),
            "difficulty": dict(Counter(t["difficulty"] for t in tasks)), "aster_import": prior.aster.__file__,
            "python": sys.executable, "same_settings_except_max_workers": True,
            "driver_hashes": driver_hashes()}, indent=2))
    elif args.mode == "run":
        if not args.sources or args.task or args.repeat or args.arm:
            raise ValueError("run_requires_sources_and_complete_fixed_schedule")
        OUT.mkdir(parents=True, exist_ok=True)
        with (OUT / "controller.lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            run(tasks, args.sources)
    else:
        if not args.task or not args.repeat or not args.arm:
            raise ValueError("episode_requires_task_repeat_arm")
        asyncio.run(episode(next(t for t in tasks if t["id"] == args.task), args.arm, args.repeat))


if __name__ == "__main__":
    main()
