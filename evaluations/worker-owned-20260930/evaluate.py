"""Frozen ten-task, three-arm, two-repeat development comparison."""
# ruff: noqa: E402
from __future__ import annotations

import argparse
import asyncio
import fcntl
import importlib.util
import itertools
import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
OUT = REPO / "artifacts/private/worker-owned-20260930"
DATA = REPO / "datasets/worker-owned-20260930/selection.json"
spec = importlib.util.spec_from_file_location("owned_prior", HERE.parent / "worker-redesign-20260928/evaluate.py")
prior = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prior)
legacy, sha, save = prior.legacy, prior.sha, prior.save
ARMS, REPEATS = ("solo", "current", "worker"), (1, 2)


def selection():
    return json.loads(DATA.read_text())


def bank():
    design, tasks = selection(), []
    if design["arms"] != list(ARMS) or design["repeats"] != len(REPEATS):
        raise ValueError("changed_experiment_design")
    for item in design["tasks"]:
        path = REPO / item["dataset"]
        if sha(path.read_bytes()) != item["dataset_sha256"]:
            raise ValueError("changed_source_dataset")
        task = next(json.loads(s) for s in path.read_text().splitlines() if json.loads(s)["id"] == item["id"])
        digest = sha(json.dumps(task, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode())
        if digest != item["task_sha256"] or sha(task["prompt"].encode()) != item["prompt_sha256"]:
            raise ValueError("changed_task_or_prompt")
        if any(task[key] != item[key] for key in ("source", "difficulty")):
            raise ValueError("changed_task_stratum")
        tasks.append(task)
    if (len(tasks) != 10 or len({t["id"] for t in tasks}) != 10
            or Counter(t["difficulty"] for t in tasks) != design["difficulty_counts"]
            or len({t["source"] for t in tasks}) != design["source_count"]):
        raise ValueError("changed_selection")
    orders = [order for item in design["tasks"] for order in item["arm_orders"]]
    counts = Counter(tuple(order) for order in orders)
    if (len(orders) != 20 or set(counts) != set(itertools.permutations(ARMS))
            or max(counts.values()) - min(counts.values()) > 1
            or any(len(item["arm_orders"]) != 2 or item["arm_orders"][0] == item["arm_orders"][1]
                   for item in design["tasks"])):
        raise ValueError("unbalanced_arm_orders")
    positions = [sum(order[i] == arm for order in orders) for arm in ARMS for i in range(3)]
    if max(positions) - min(positions) > 1:
        raise ValueError("unbalanced_arm_positions")
    return tasks


def schedule(tasks):
    orders = {item["id"]: item["arm_orders"] for item in selection()["tasks"]}
    return [(task, repeat, arm) for task in tasks for repeat in REPEATS
            for arm in orders[task["id"]][repeat - 1]]


def gates():
    design, result = selection(), {}
    for filename, digest in design["unchanged_grader_files"].items():
        if sha((REPO / filename).read_bytes()) != digest:
            raise ValueError("reused_grader_changed: " + filename)
    for item in design["tasks"]:
        path = REPO / item["preflight"]["path"]
        if sha(path.read_bytes()) != item["preflight"]["sha256"]:
            raise ValueError("changed_preflight_gate")
        gate = json.loads(path.read_text())
        reference = gate["reference"]
        count = reference.get("required_count", reference.get("expected", reference.get("expected_tests")))
        if (not gate["ready"] or gate["starter"]["passed"] or not reference["passed"]
                or reference.get("missing_tests") or count != item["preflight"]["required_tests"]):
            raise ValueError("preflight_not_ready")
        gate["owned_reused_gate"] = item["preflight"]
        result[item["id"]] = gate
    return result


def settings(arm):
    return legacy.ledger_driver.config(max_workers=0 if arm == "solo" else 2,
        memory_enabled=False, memory_evolution=False, auto_dream=False)


def driver_hashes():
    # Explicit entrypoints: later diagnostics/reports cannot silently change a paid run's identity.
    paths = {HERE / "evaluate.py", HERE / "summarize.py", HERE / "PROTOCOL.md", DATA,
             Path(prior.__file__), Path(legacy.__file__), Path(legacy.ledger_driver.__file__),
             prior.OLD / "control_adapter.py", prior.OLD / "terminal_adapter.py",
             prior.OLD / "repository_adapter.py", prior.OLD / "execution_environment.py",
             REPO / "datasets/worker-code-v2/checks/verify.py"}
    paths.update(REPO / name for name in selection()["unchanged_grader_files"])
    for item in selection()["tasks"]:
        dataset = REPO / item["dataset"]
        paths.add(dataset)
        for name in ("manifest.json", "sources.json", "PREPARATION.json", "environment.json"):
            if (dataset.parent / name).exists():
                paths.add(dataset.parent / name)
    return {str(path.relative_to(REPO)): sha(path.read_bytes()) for path in sorted(paths)}


def binding(path):
    sources = json.loads(path.read_text())
    if (set(sources) != set(ARMS) or sources["solo"] != sources["worker"]
            or sources["current"]["commit"] != selection()["current_commit"]
            or sources["solo"]["commit"] == sources["current"]["commit"]):
        raise ValueError("require_old_current_and_same_new_solo_worker")
    if any(str(Path(item["root"]).resolve()) != item["root"] for item in sources.values()):
        raise ValueError("source_roots_must_be_absolute_canonical_paths")
    return sources


def source_identity(source):
    root, commit = Path(source["root"]), source["commit"]
    if subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip() != commit:
        raise ValueError("source_head_differs_from_binding")
    if (subprocess.check_output(["git", "diff", commit, "--", "src/aster"], cwd=root)
            or subprocess.check_output(["git", "ls-files", "--others", "--exclude-standard", "src/aster"], cwd=root)):
        raise ValueError("source_not_clean_at_binding")
    hashes = legacy.ledger_driver.hashes(root / "src/aster", "**/*.py")
    if any(hashes.get(name) != digest for name, digest in selection()["grader_support_hashes"].items()):
        raise ValueError("aster_grader_support_changed")
    return hashes


def identity(sources):
    configs = {arm: settings(arm) for arm in ARMS}
    executables = {sys.executable}
    for task in bank():
        adapter = legacy.adapter(task)
        if task["source"] == "terminal-bench":
            executables.add(str(adapter._python(task)))
        elif task["source"].startswith("swebench"):
            executables.add(str(adapter._runtime(task) / "bin/python"))
    environments = {}
    for executable in sorted(executables):
        packages = subprocess.check_output([executable, "-c", "import importlib.metadata as m,json; "
            "print(json.dumps(sorted((d.metadata['Name'],d.version) for d in m.distributions())))"])
        environments[executable] = {"python_sha256": sha(Path(executable).read_bytes()), "packages_sha256": sha(packages)}
    return {"sources": sources, "source_hashes": {arm: source_identity(source) for arm, source in sources.items()},
            "driver_hashes": driver_hashes(), "settings": {arm: config.identity() for arm, config in configs.items()},
            "endpoint_sha256": {arm: sha(config.base_url.encode()) for arm, config in configs.items()},
            "environments": environments,
            "supplementary_freeze_sha256": (sha((HERE / "diagnostics/freeze.json").read_bytes())
                if (HERE / "diagnostics/freeze.json").exists() else None),
            "gates": {task: gate["owned_reused_gate"] for task, gate in gates().items()}}


async def episode(task, arm, repeat):
    frozen = json.loads((OUT / "run-lock.json").read_text())
    if frozen != identity(frozen["sources"]):
        raise ValueError("changed_frozen_run_inputs")
    source = frozen["sources"][arm]
    if str(prior.SOURCE_ROOT) != source["root"] or prior.source_args.source_commit != source["commit"]:
        raise ValueError("wrong_episode_source_binding")
    legacy.OUT, legacy.CACHE = OUT / f"repeat-{repeat:02d}", prior.CACHE
    save(legacy.OUT / "preflight" / task["id"] / "preflight.json", gates()[task["id"]])
    legacy.SOURCE, legacy.loaded_source = source["commit"], prior.loaded_source
    legacy.driver_hashes, legacy.settings = driver_hashes, settings
    budget = legacy.LIMITS[task["difficulty"]]["reported_tokens"]
    legacy.TaskSpec = lambda **kwargs: prior.TaskSpec(**kwargs, max_tokens=budget)
    if prior.loaded_source() != frozen["source_hashes"][arm]:
        raise ValueError("loaded_source_differs_from_lock")
    await legacy.episode(task, arm)
    output = legacy.OUT / "runs" / (task["id"] + "--" + arm)
    for filename in ("launch.json", "result.json"):
        row = json.loads((output / filename).read_text())
        row.update(repeat=repeat, evaluation_split="development_seen", source_root=source["root"],
                   source_commit=source["commit"], internal_team_token_budget=budget,
                   preflight_sha256=frozen["gates"][task["id"]]["sha256"])
        save(output / filename, row)
    if frozen != identity(frozen["sources"]):
        raise ValueError("run_inputs_changed_during_episode")


def audit_sources(sources):
    for arm, source in sources.items():
        subprocess.run([sys.executable, str(__file__), "audit", "--arm", arm,
            "--source-root", source["root"], "--source-commit", source["commit"]], check=True,
            cwd=REPO, env=dict(os.environ, PYTHONPATH=str(Path(source["root"]) / "src")))


def run(tasks, sources):
    if not (HERE / "diagnostics/freeze.json").is_file():
        raise ValueError("supplementary_protocol_must_be_frozen_before_paid_run")
    audit_sources(sources)
    current = identity(sources)
    lockfile = OUT / "run-lock.json"
    if lockfile.exists() and json.loads(lockfile.read_text()) != current:
        raise ValueError("run_already_frozen_with_different_inputs")
    for name, digest in current["driver_hashes"].items():
        target = OUT / "driver-snapshots" / digest
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((REPO / name).read_bytes())
    save(lockfile, current)
    (OUT / "driver-snapshots" / current["supplementary_freeze_sha256"]).write_bytes(
        (HERE / "diagnostics/freeze.json").read_bytes())
    save(OUT / "source-bindings.json", sources)
    for task, repeat, arm in schedule(tasks):
        name = task["id"] + "--" + arm
        output = OUT / f"repeat-{repeat:02d}" / "runs" / name
        if (output / "result.json").exists():
            print("existing_result", repeat, name, flush=True)
            continue
        if output.exists():
            raise RuntimeError("incomplete_episode_requires_audit: " + str(output))
        source = sources[arm]
        log = OUT / "logs" / f"repeat-{repeat:02d}--{name}.txt"
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("w") as stream:
            process = subprocess.run([sys.executable, str(__file__), "episode", "--task", task["id"],
                "--repeat", str(repeat), "--arm", arm, "--source-root", source["root"],
                "--source-commit", source["commit"]], cwd=REPO,
                env=dict(os.environ, PYTHONPATH=str(Path(source["root"]) / "src")),
                stdout=stream, stderr=subprocess.STDOUT)
        print("episode", repeat, name, "exit", process.returncode, flush=True)
        if process.returncode:
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
    gates()
    if "max_tokens" not in prior.TaskSpec.model_fields:
        raise RuntimeError("all_arms_require_internal_team_token_budget")
    if args.mode == "audit":
        prior.check_cache()
        if sys.version_info[:2] != (3, 12) or "aster" not in sys.prefix:
            raise RuntimeError("use_conda_aster_or_derived_python312")
        if args.sources:
            sources = binding(args.sources)
            audit_sources(sources)
            identity(sources)
        if args.source_commit:
            if not args.arm:
                raise ValueError("bound_audit_requires_arm")
            if args.arm == "current" and args.source_commit != selection()["current_commit"]:
                raise ValueError("wrong_current_commit")
            prior.loaded_source()
            source_identity({"root": str(prior.SOURCE_ROOT), "commit": args.source_commit})
        configs = {arm: settings(arm).identity() for arm in ARMS}
        if any({k: v for k, v in config.items() if k != "max_workers"} !=
               {k: v for k, v in configs["solo"].items() if k != "max_workers"} for config in configs.values()):
            raise ValueError("settings_differ_beyond_worker_availability")
        print(json.dumps({"tasks": len(tasks), "episodes": len(schedule(tasks)), "reused_gates": len(gates()),
            "difficulty": dict(Counter(t["difficulty"] for t in tasks)), "sources": len({t["source"] for t in tasks}),
            "aster_import": prior.aster.__file__, "python": sys.executable,
            "source_bound": bool(args.sources or args.source_commit), "driver_hashes": driver_hashes()}, indent=2))
    elif args.mode == "run":
        if not args.sources or args.task or args.repeat or args.arm:
            raise ValueError("run_requires_sources_and_complete_fixed_schedule")
        OUT.mkdir(parents=True, exist_ok=True)
        with (OUT / "controller.lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            run(tasks, binding(args.sources))
    else:
        if not args.task or not args.repeat or not args.arm:
            raise ValueError("episode_requires_task_repeat_arm")
        asyncio.run(episode(next(t for t in tasks if t["id"] == args.task), args.arm, args.repeat))


if __name__ == "__main__":
    main()
