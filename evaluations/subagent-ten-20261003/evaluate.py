"""Frozen ten-task rounds, paired real-model runs, and whole-team accounting."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.metadata
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path
from statistics import median

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
OUT = REPO / "artifacts/private/subagent-ten-20261003"
DATA = REPO / "datasets/subagent-ten-20261003"
LIMITS = dict(model_calls=36, admitted_tools=60, reported_tokens=300000, seconds=600)


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


shared = load("ten_shared", HERE.parent / "subagent-live-20261002/evaluate.py")
cases = load("ten_cases", HERE / "cases.py")
scoring = load("ten_scoring", HERE.parent / "subagent-wait-modes-20261002/scoring.py")
digest, hashes = shared.digest, shared.hashes


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix="." + path.name + ".", delete=False) as temporary:
        staged = Path(temporary.name)
    try:
        staged.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
        staged.replace(path)
    finally:
        staged.unlink(missing_ok=True)


def completed_result(path, expected, frozen):
    try:
        row = json.loads(path.read_text())
        if not isinstance(row, dict) or any(row[k] != value for k, value in expected.items()):
            raise ValueError("result_identity_mismatch")
        launch = json.loads(path.with_name("launch.json").read_text())
        identity = expected | dict(source=frozen["source"], settings=arm_identity(frozen, expected["arm"]))
        if any(launch[key] != value for key, value in identity.items()):
            raise ValueError("launch_identity_mismatch")
        if frozen.get("result_identity") == "freeze_sha256":
            fingerprint = digest(path.parents[2] / "freeze.json")
            if row["freeze_sha256"] != fingerprint or launch["freeze_sha256"] != fingerprint:
                raise ValueError("result_freeze_mismatch")
        return row
    except (OSError, ValueError, KeyError, TypeError):
        raise RuntimeError("invalid_result_requires_audit: " + str(path)) from None


def arm_settings(config, strategy, arm):
    return config.model_copy(update={"max_workers": 1}) if strategy == "adaptive" and arm == "team" else config


def arm_identity(frozen, arm):
    return frozen["arm_settings"][arm] if frozen.get("strategy") == "adaptive" else frozen["settings"]


def grade(base, answer, gold, review):
    try:
        return base.grade(answer, gold, review)
    except TypeError:
        # The historical grader assumes citation symbols are hashable strings.
        return base.grade("", gold, review)


def usage_complete(row):
    return ("total_tokens" in row["usage"]
            and row["requests_without_usage"] == row["http_without_usage"] == 0)


def parent_input_complete(row):
    return row.get("parent_input_complete", bool(row["parent_model_calls"])
                   and usage_complete(row)
                   and bool(row["usage"].get("usage_requests"))
                   and row["usage"].get("input_tokens_requests") == row["usage"]["usage_requests"])


def harness_hashes():
    paths = [HERE / "evaluate.py", HERE / "cases.py", HERE / "PROTOCOL.md",
             Path(scoring.__file__), *sorted(cases.UPSTREAM.glob("*.json"))]
    return shared.harness_hashes() | {str(p.relative_to(REPO)): digest(p) for p in paths}


def environment():
    packages = sorted((d.metadata["Name"], d.version) for d in importlib.metadata.distributions())
    return dict(python=sys.executable, version=sys.version,
                packages_sha256=hashlib.sha256(json.dumps(packages).encode()).hexdigest())


def prepare(base, task, root, strategy=None):
    cases.prepare(base, task, root)
    request = base.prompt(task)
    if strategy == "adaptive":
        external = ("The two work packages below are independently assigned; both must be covered. "
                    "Do not start additional subagents: the experiment controls assignment externally.")
        assert request.count(external) == 1, "assignment_instruction_changed"
        request = request.replace(external, "The two work packages below are independent; both must be covered.")
        (root / "REQUEST.md").write_text(request)
    return request


def preflight(base, strategy=None):
    records = []
    for task in cases.bank(base):
        with tempfile.TemporaryDirectory(prefix="aster-ten-oracle-") as folder:
            root = Path(folder) / "workspace"
            prepare(base, task, root, strategy)
            gold = cases.oracle(base, task, root, scoring.ranges)
            answers = [dict(id=row["id"], observed=row["observed"], evidence=dict(
                path=row["evidence_path"], symbol=row["evidence_symbol"], line=row["lines"][0]),
                **({"regression": row["regression"]} if task["kind"] == "review" else {}))
                for row in gold]
            review = task["kind"] == "review"
            assert base.grade(json.dumps({"answers": answers}), gold, review)["passed"]
            negatives = [answers[:-1], answers + [answers[0]]]
            for field, value in (("observed", "__wrong__"), ("path", "__wrong__.py"),
                                 ("line", 0), ("line", gold[0]["lines"][1] + 1),
                                 ("symbol", "__missing__"),
                                 ("symbol", gold[0]["evidence_symbol"] + " / other")):
                wrong = json.loads(json.dumps(answers))
                (wrong[0] if field == "observed" else wrong[0]["evidence"])[field] = value
                negatives.append(wrong)
            assert not any(base.grade(json.dumps({"answers": value}), gold, review)["passed"]
                           for value in negatives)
            assert not base.grade('{"answers":invalid}', gold, review)["passed"]
            assert len(gold) == 8
            if review:
                assert sum(r["regression"] for r in gold) == 4
            records.append(dict(task=task["id"], gold=gold, negative_controls=len(negatives) + 1,
                                public_hashes=base.ledger_driver.product_hashes(root)))
    return records


def freeze(round_id, source, repeats, strategy="two_children"):
    dest = OUT / round_id
    assert not (dest / "freeze.json").exists() and not (dest / "runs").exists()
    if (dest / "src").exists():
        expected = hashes(source)
        assert all(expected.get(name) == sha for name, sha in hashes(dest / "src").items()), "incomplete_freeze_source_changed"
    shutil.copytree(source, dest / "src", dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__"))
    base = shared.base(dest / "src")
    records = preflight(base, strategy)
    save(dest / "preflight.json", records)
    tasks = cases.bank(base)
    if not (DATA / "instances.json").exists():
        save(DATA / "instances.json", tasks)
        save(DATA / "manifest.json", dict(tasks=10, facts=80, source_snapshots=7,
                                          sha256=digest(DATA / "instances.json")))
    assert tasks == json.loads((DATA / "instances.json").read_text())
    config = base.settings("parallel")
    assert config.api_key.get_secret_value() or config.auth == "none"
    order = [dict(task=task["id"], arm=arm, repeat=repeat)
             for repeat in range(1, repeats + 1) for n, task in enumerate(tasks)
             for arm in (("solo", "team") if (n + repeat) % 2 else ("team", "solo"))]
    frozen = dict(round=round_id, strategy=strategy, result_identity="freeze_sha256", source=hashes(dest / "src"), harness=harness_hashes(),
                  dataset_sha256=digest(DATA / "instances.json"), environment=environment(),
                  settings=config.identity(), endpoint_sha256=hashlib.sha256(config.base_url.encode()).hexdigest(),
                  limits=LIMITS, order=order, preflight_sha256=digest(dest / "preflight.json"))
    if strategy == "adaptive":
        frozen["arm_settings"] = {arm: arm_settings(config, strategy, arm).identity() for arm in ("solo", "team")}
    for relative in frozen["harness"]:
        target = dest / "original-harness" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / relative, target)
    assert all(digest(dest / "original-harness" / name) == sha for name, sha in frozen["harness"].items())
    save(HERE / round_id / "FREEZE.json", frozen)
    save(HERE / round_id / "PREFLIGHT.json", [{k: v for k, v in r.items() if k != "gold"} for r in records])
    save(dest / "freeze.json", frozen)
    print(f"{round_id}: frozen {len(order)} episodes, 80 facts, 90 negative controls", flush=True)


def inputs(round_id):
    dest = OUT / round_id
    frozen = json.loads((dest / "freeze.json").read_text())
    assert hashes(dest / "src") == frozen["source"]
    assert harness_hashes() == frozen["harness"]
    assert all(digest(dest / "original-harness" / name) == sha for name, sha in frozen["harness"].items())
    assert environment() == frozen["environment"]
    assert digest(DATA / "instances.json") == frozen["dataset_sha256"]
    assert digest(dest / "preflight.json") == frozen["preflight_sha256"]
    base = shared.base(dest / "src")
    config = base.settings("parallel")
    assert config.identity() == frozen["settings"]
    for arm in ("solo", "team"):
        assert arm_settings(config, frozen.get("strategy"), arm).identity() == arm_identity(frozen, arm)
    assert hashlib.sha256(config.base_url.encode()).hexdigest() == frozen["endpoint_sha256"]
    return dest, frozen, base, config


async def episode(round_id, task_id, arm, repeat):
    dest, frozen, base, config = inputs(round_id)
    config = arm_settings(config, frozen.get("strategy"), arm)
    from aster.schema import ToolCall

    task = next(t for t in cases.bank(base) if t["id"] == task_id)
    checked = next(r for r in json.loads((dest / "preflight.json").read_text()) if r["task"] == task_id)
    name = f"{task_id}--{arm}--{repeat}"
    output, root = dest / "runs" / name, dest / "workspaces" / name
    output.mkdir(parents=True, exist_ok=False)
    request = prepare(base, task, root, frozen.get("strategy"))
    before = base.ledger_driver.product_hashes(root)
    assert before == checked["public_hashes"]
    base.ledger_driver.LIMITS = LIMITS
    ledger = base.ledger_driver.Ledger(name, output)
    runtime = base.Runtime(root, config, hooks=base.Hooks([ledger]))
    runtime.start(base.TaskSpec(prompt=request, max_steps=LIMITS["admitted_tools"],
                               max_turns=LIMITS["model_calls"], max_tokens=LIMITS["reported_tokens"],
                               require_observation=True, require_edit=False, require_verification=False))
    fingerprint = digest(dest / "freeze.json")
    save(output / "launch.json", dict(task=task_id, arm=arm, repeat=repeat, freeze_sha256=fingerprint,
                                     settings=config.identity(), source=frozen["source"]))
    assignments = []

    async def execute():
        if arm == "team" and frozen.get("strategy") != "adaptive":
            overlap = frozen.get("strategy") == "parent_child"
            groups = task["groups"][:1] if overlap else task["groups"]
            for group in groups:
                assignment = ("Complete only work package " + group["name"] + ". Questions: "
                              + ", ".join(q["id"] for q in group["questions"])
                              + ". Return all assigned answer rows with source evidence using the original JSON contract. "
                              "Do not answer the other package.")
                result = await runtime.workers.start(
                    assignment, mode="explore", explicit=True,
                    parent_work=(
                        "Independently solve package " + task["groups"][1]["name"] + ": "
                        + ", ".join(q["id"] for q in task["groups"][1]["questions"])
                        + ". Then integrate the child's other package into the complete JSON response."
                        if overlap else
                        "Integrate both independent evidence reports into the final JSON response."))
                assert result.get("status") == "running", result
                assignments.append(result)
            if not overlap:
                waited = await runtime.step(ToolCall(name="worker_status", arguments={
                    "worker_id": [r["worker_id"] for r in assignments], "wait_seconds": 300, "wait_for": "all",
                }))
                assert waited.status == "success", waited.reason
        await runtime.run()

    started, error = time.monotonic(), None
    try:
        with base.observe(ledger, runtime):
            await asyncio.wait_for(execute(), timeout=LIMITS["seconds"])
    except (Exception, asyncio.CancelledError) as exc:
        error = type(exc).__name__
    finally:
        await runtime.workers.close()
        runtime.sandbox.close()
    seconds = time.monotonic() - started
    runs, tools, _, usage = base.ledger_driver.collect(runtime, set())
    score = grade(base, runtime.state.final, checked["gold"], task["kind"] == "review")
    intact = before == base.ledger_driver.product_hashes(root)
    source_intact = hashes(dest / "src") == frozen["source"]
    children = [r for r in runs if r.get("parent_run_id")]
    requests = ledger.requests
    parent_calls = [r for r in requests if r.get("role") == "parent"]
    attempts = [a for r in requests for a in r["attempts"]]
    known = Counter()
    for request in requests:
        known.update(request.get("usage", {}))
    checks = score.get("checks", [])
    finishes = [t for t in tools if t["name"] == "worker_finish"]
    result = dict(task=task_id, arm=arm, repeat=repeat, freeze_sha256=fingerprint, seconds=seconds, grade=score,
                  passed=score["passed"] and runtime.state.phase == "completed" and intact and source_intact and error is None,
                  behavior_correct=sum(c["observed"] and c["regression"] for c in checks),
                  phase=runtime.state.phase, stop_reason=runtime.state.stop_reason, error=error,
                  workspace_unchanged=intact, source_unchanged=source_intact,
                  usage=usage, ledger_usage=dict(known), usage_matches=usage == dict(known),
                  model_calls=len(requests), parent_model_calls=len(parent_calls),
                  parent_input_tokens=sum(r.get("usage", {}).get("input_tokens", 0) for r in parent_calls),
                  parent_input_complete=bool(parent_calls) and all(
                      "input_tokens" in r.get("usage", {})
                      and all("input_tokens" in a.get("usage", {}) for a in r["attempts"])
                      for r in parent_calls),
                  requests_without_usage=sum("total_tokens" not in r.get("usage", {}) for r in requests),
                  http_classifications=dict(Counter(a.get("classification", "") for a in attempts)),
                  response_models=dict(Counter(a.get("response_model", "") for a in attempts)),
                  http_attempts=len(attempts),
                  http_without_usage=sum("total_tokens" not in a.get("usage", {}) for a in attempts),
                  parent_tools=dict(Counter(t["name"] for t in tools if not t["child"])),
                  tool_calls=len(tools), finish_attempts=len(finishes),
                  finish_rejected=sum(t["status"] != "success" for t in finishes),
                  workers_started=len(children), workers_completed=sum(r["phase"] == "completed" for r in children),
                  final=runtime.state.final, assignments=assignments, runs=runs, tools=tools)
    save(output / "result.json", result)
    print(json.dumps({k: result[k] for k in ("task", "arm", "repeat", "passed", "behavior_correct", "seconds", "usage", "error")}), flush=True)


def summarize(round_id):
    dest = OUT / round_id
    frozen = json.loads((dest / "freeze.json").read_text())
    results = []
    for row in frozen["order"]:
        path = dest / "runs" / f"{row['task']}--{row['arm']}--{row['repeat']}" / "result.json"
        if path.exists():
            results.append(completed_result(path, row, frozen))
    summary = {}
    for arm in ("solo", "team"):
        rows = [r for r in results if r["arm"] == arm]
        summary[arm] = dict(episodes=len(rows), passed=sum(r["passed"] for r in rows),
                            strict_facts=sum(r["grade"]["correct"] for r in rows),
                            behavior_facts=sum(r["behavior_correct"] for r in rows),
                            total_facts=8 * len(rows),
                            parse_errors=sum(r["grade"]["parse_error"] for r in rows),
                            tokens=sum(r["usage"].get("total_tokens", 0) for r in rows),
                            usage_complete=bool(rows) and all(usage_complete(r) for r in rows),
                            incomplete_usage_episodes=sum(not usage_complete(r) for r in rows),
                            parent_input_complete=bool(rows) and all(parent_input_complete(r) for r in rows),
                            **{key: sum(r[key] for r in rows) for key in (
                                "seconds", "parent_input_tokens", "parent_model_calls", "model_calls",
                                "requests_without_usage", "http_without_usage", "finish_rejected")})
    pairs = []
    for solo in (r for r in results if r["arm"] == "solo"):
        team = next((r for r in results if r["arm"] == "team" and r["task"] == solo["task"]
                     and r["repeat"] == solo["repeat"]), None)
        if team:
            complete = usage_complete(team) and usage_complete(solo)
            pairs.append(dict(task=solo["task"], repeat=solo["repeat"],
                              win=int(team["passed"]) - int(solo["passed"]),
                              usage_complete=complete,
                              seconds=team["seconds"] / solo["seconds"] if solo["seconds"] else None,
                              parent_input_tokens=team["parent_input_tokens"] / solo["parent_input_tokens"]
                              if complete and parent_input_complete(team) and parent_input_complete(solo)
                              and solo["parent_input_tokens"] else None,
                              tokens=team["usage"]["total_tokens"] / solo["usage"]["total_tokens"]
                              if complete and solo["usage"]["total_tokens"] else None))
    summary["paired"] = dict(n=len(pairs), wins=sum(p["win"] > 0 for p in pairs),
                             losses=sum(p["win"] < 0 for p in pairs),
                             **{key + "_ratio_median": median([p[key] for p in pairs if p[key] is not None])
                                if any(p[key] is not None for p in pairs) else None
                                for key in ("tokens", "seconds", "parent_input_tokens")})
    save(HERE / round_id / "results.json", results)
    save(HERE / round_id / "summary.json", summary)
    save(HERE / round_id / "pairs.json", pairs)
    print(json.dumps(summary, indent=2), flush=True)


def run(round_id):
    frozen = json.loads((OUT / round_id / "freeze.json").read_text())
    for row in frozen["order"]:
        target = OUT / round_id / "runs" / f"{row['task']}--{row['arm']}--{row['repeat']}"
        if (target / "result.json").exists():
            completed_result(target / "result.json", row, frozen)
            continue
        if target.exists():
            raise RuntimeError("unfinished_episode_requires_audit: " + str(target))
        subprocess.run([sys.executable, str(__file__), "episode", round_id,
                        "--task", row["task"], "--arm", row["arm"], "--repeat", str(row["repeat"])],
                       check=True)
        result = completed_result(target / "result.json", row, frozen)
        summarize(round_id)
        if result["requests_without_usage"] == result["model_calls"] and result["model_calls"]:
            raise RuntimeError("provider_unavailable; stopped after preserved failure")
    summarize(round_id)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("freeze", "run", "episode", "summarize"))
    parser.add_argument("round")
    parser.add_argument("--source", type=Path, default=REPO / "src")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--strategy", choices=("two_children", "parent_child", "adaptive"), default="two_children")
    parser.add_argument("--task")
    parser.add_argument("--arm", choices=("solo", "team"))
    parser.add_argument("--repeat", type=int, default=1)
    args = parser.parse_args()
    if args.command == "freeze":
        freeze(args.round, args.source.resolve(), args.repeats, args.strategy)
    elif args.command == "episode":
        asyncio.run(episode(args.round, args.task, args.arm, args.repeat))
    elif args.command == "run":
        run(args.round)
    else:
        summarize(args.round)
