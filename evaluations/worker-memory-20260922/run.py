"""Real GPT ablations on frozen Aster, with whole-tree accounting and external grading."""

# ruff: noqa: E402
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from collections import Counter
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import benchmark_support as support
import cases

import aster
from aster.config import load_settings
from aster.core.hooks import HookResult, Hooks
from aster.core.runtime import Runtime
from aster.providers.client import ModelClient
from aster.providers.errors import ProviderError
from aster.schema import TaskSpec
from aster.storage.workspace import Workspace

SOURCE = "7462e8b"
LIMITS = {"model_calls": 40, "admitted_tools": 60, "reported_tokens": 500000, "seconds": 600}


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def hashes(root, pattern="**/*"):
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.glob(pattern))
        if p.is_file()
        and not p.is_symlink()
        and "__pycache__" not in p.parts
        and ".pytest_cache" not in p.parts
    }


def product_hashes(root):
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in Workspace(root).files()
    }


def put_files(root, files):
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


def config(**overrides):
    return load_settings(
        REPO / ".env",
        context_tokens=64000,
        output_tokens=8192,
        stream=True,
        request_timeout=120,
        approval="auto",
        sandbox="required",
        network=False,
        read_parallelism=1,
        completion="strict",
        **overrides,
    )


def verify_source():
    assert (
        Path(sys.executable).resolve() == Path("/home/dzh/.conda/envs/aster/bin/python").resolve()
    )
    assert Path(aster.__file__).resolve() == REPO / "src/aster/__init__.py"
    assert not subprocess.check_output(["git", "diff", SOURCE, "--", "src/aster"], cwd=REPO)
    return hashes(REPO / "src/aster", "**/*.py")


class Ledger:
    """Evaluation-only ceilings, shared by the real parent and its real Workers."""

    def __init__(self, name, output):
        self.name, self.output = name, output
        self.requests = []
        self.tools = 0

    def before_tool(self, state, call):
        if self.tools >= LIMITS["admitted_tools"]:
            return HookResult("stop", "evaluation_whole_tree_tool_budget")
        self.tools += 1
        return HookResult()

    def after_tool(self, state, call, outcome):
        print(
            json.dumps(
                {
                    "name": self.name,
                    "run": state.run_id[:8],
                    "tool": call.name,
                    "status": outcome.status,
                    "child": bool(state.parent_run_id),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )

    @contextmanager
    def observe(self):
        original = ModelClient.complete
        ledger = self

        async def complete(client, messages, tools, *, on_event=None):
            if (
                len(ledger.requests) >= LIMITS["model_calls"]
                or sum(r.get("usage", {}).get("total_tokens", 0) for r in ledger.requests)
                >= LIMITS["reported_tokens"]
            ):
                raise ProviderError(
                    "evaluation_whole_tree_model_budget", category="evaluation_budget"
                )
            row = {"started_seconds": time.monotonic(), "status": "pending", "attempts": []}
            ledger.requests.append(row)

            def event(e):
                if e.get("type") == "diagnostic":
                    row["attempts"].append(e)
                if on_event:
                    on_event(e)

            try:
                reply = await original(client, messages, tools, on_event=event)
                row.update(status="completed", response_model=reply.provider_model)
                return reply
            except BaseException as exc:
                row.update(status="failed", error_type=type(exc).__name__)
                raise
            finally:
                row.update(
                    seconds=time.monotonic() - row["started_seconds"], usage=dict(client.last_usage)
                )
                save(ledger.output / "request-ledger.json", ledger.requests)

        ModelClient.complete = complete
        try:
            yield
        finally:
            ModelClient.complete = original


def collect(runtime, excluded):
    root = runtime.workspace.root
    runs = []
    tools = []
    memories = []
    total = Counter()
    for path in sorted((root / ".aster/runs").glob("*/checkpoint.json")):
        if path.parent.name in excluded:
            continue
        state = json.loads(path.read_text())["state"]
        events = runtime.evidence.events(state["run_id"])
        results = [e for e in events if e["event"] == "tool_result"]
        total.update(state["usage"])
        for e in results:
            tools.append(
                {
                    "run_id": state["run_id"],
                    "child": bool(state.get("parent_run_id")),
                    "name": e["call"]["name"],
                    "arguments": e["call"]["arguments"],
                    "status": e["outcome"]["status"],
                    "reason": e["outcome"]["reason"],
                    "changed_paths": e["outcome"]["changed_paths"],
                    "seq": e["seq"],
                }
            )
        memories.extend(
            {
                "run_id": state["run_id"],
                "seq": e["seq"],
                **{
                    k: v
                    for k, v in e.items()
                    if k in {"ids", "count", "status", "checked", "deactivated", "reason", "event"}
                },
            }
            for e in events
            if e["event"] in {"memory_recalled", "memory_dream"}
        )
        runs.append(
            {
                k: state.get(k)
                for k in [
                    "run_id",
                    "parent_run_id",
                    "phase",
                    "stop_reason",
                    "step",
                    "turn",
                    "usage",
                    "changed_paths",
                    "verification",
                ]
            }
        )
    return runs, tools, memories, dict(total)


async def execute(name, root, settings, prompt, out, grader_path=None, learning=False):
    source_before = verify_source()
    out.mkdir(parents=True, exist_ok=False)
    excluded = {p.name for p in (root / ".aster/runs").glob("*")}
    before = product_hashes(root)
    ledger = Ledger(name, out)
    runtime = Runtime(root, settings, hooks=Hooks([ledger]))
    memory_before = runtime.memory.state.copy()
    save(out / "memory-before.json", memory_before)
    save(
        out / "launch.json",
        {
            "name": name,
            "source": SOURCE,
            "settings": settings.identity(),
            "prompt": prompt,
            "product": before,
            "limits": LIMITS,
            "time": datetime.now(UTC).isoformat(),
            "python": sys.executable,
            "import": aster.__file__,
        },
    )
    started = time.monotonic()
    error = None
    try:
        with ledger.observe():
            await asyncio.wait_for(
                runtime.run(
                    TaskSpec(
                        prompt=prompt,
                        max_steps=60,
                        max_turns=40,
                        require_observation=True,
                        require_edit=not learning,
                        require_verification=not learning,
                    )
                ),
                LIMITS["seconds"],
            )
    except (Exception, asyncio.CancelledError) as exc:
        error = type(exc).__name__
    finally:
        await runtime.workers.close()
        runtime.sandbox.close()
    seconds = time.monotonic() - started
    runs, tools, memory_events, usage = collect(runtime, excluded)
    after = product_hashes(root)
    immutable = {k: v for k, v in before.items() if k == "README.md" or k.startswith("docs/")}
    intact = all(after.get(k) == v for k, v in immutable.items())
    grade = await support.external_check(root, grader_path) if grader_path else None
    state = runtime.state
    known = Counter()
    for row in ledger.requests:
        known.update(row.get("usage", {}))
    if dict(known) != usage:
        save(out / "accounting-mismatch.json", {"ledger": dict(known), "runs": usage})
    child_nodes = [r for r in runs if r.get("parent_run_id")]
    attempts = [a for r in ledger.requests for a in r["attempts"]]
    result = {
        "name": name,
        "phase": state.phase,
        "stop_reason": state.stop_reason,
        "error": error,
        "passed": state.phase == "completed" and intact and (grade is None or grade["passed"]),
        "grade": grade,
        "seconds": seconds,
        "usage": usage,
        "ledger_usage": dict(known),
        "usage_matches": dict(known) == usage,
        "model_calls": len(ledger.requests),
        "http_attempts": len(attempts),
        "http_with_total_usage": sum("total_tokens" in a.get("usage", {}) for a in attempts),
        "response_models": dict(Counter(a.get("response_model", "") for a in attempts)),
        "diagnostic_flags": dict(Counter(f for a in attempts for f in a.get("flags", []))),
        "runs": runs,
        "tools": tools,
        "memory_events": memory_events,
        "workers_started": len(child_nodes),
        "workers_completed": sum(n["phase"] == "completed" for n in child_nodes),
        "coordination_calls": sum(t["name"].startswith("worker_") for t in tools),
        "scope_conflicts": sum(t["reason"] == "worker_scope_busy" for t in tools),
        "memory_store_attempts": sum(t["name"] == "memory_store" for t in tools),
        "memory_store_successes": sum(
            t["name"] == "memory_store" and t["status"] == "success" for t in tools
        ),
        "doc_read_calls": sum(
            t["name"] == "read_file"
            and t["arguments"].get("path", "").lstrip("./").startswith("docs/")
            for t in tools
        ),
        "read_calls": sum(t["name"] == "read_file" for t in tools),
        "search_calls": sum(t["name"] == "search" for t in tools),
        "shell_calls": sum(t["name"] == "run_shell" for t in tools),
        "product_before": before,
        "product_after": after,
        "docs_unchanged": intact,
        "source_unchanged": source_before == verify_source(),
        "final": state.final,
        "lessons": runtime.memory.state.get("lessons", []),
        "workspace": str(root),
    }
    save(out / "result.json", runtime.evidence.redactor.value(result))
    print(
        json.dumps(
            {
                k: result[k]
                for k in [
                    "name",
                    "passed",
                    "seconds",
                    "usage",
                    "workers_started",
                    "memory_store_successes",
                ]
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return result


def memory_modules(project, stage):
    modules = [dict(m) for m in project["tasks"][: min(stage + 1, 2)]]
    if stage == 2:
        modules[0] = project["tasks"][2]
        if project["id"] == "agenda":
            modules[1]["reference"] = modules[1]["reference"].replace("['entries']", "['events']")
    return modules


async def preflight(output):
    output.mkdir(parents=True, exist_ok=False)
    support.DATASETS = output / "graders"
    support.DATASETS.mkdir()
    checks = []
    configurations = []
    for c in cases.worker_cases():
        configurations.append((c["id"], c["files"], c["modules"]))
    for p in cases.finish_projects():
        for stage in range(3):
            configurations.append((f"{p['id']}-{stage}", p["files"], memory_modules(p, stage)))
    for ident, files, modules in configurations:
        grade_name = ident + ".py"
        (support.DATASETS / grade_name).write_text(cases.grader(modules))
        with tempfile.TemporaryDirectory(prefix="aster-wm-preflight-") as folder:
            root = Path(folder)
            put_files(root, files)
            initial = await support.external_check(root, grade_name)
            put_files(root, {m["path"]: m["reference"] for m in modules})
            good = await support.external_check(root, grade_name)
            mutations = []
            for m in modules:
                (root / m["path"]).write_text("# Deleted public implementation.\n")
                mutations.append(await support.external_check(root, grade_name))
                (root / m["path"]).write_text(m["reference"])
            row = {
                "id": ident,
                "initial": initial,
                "reference": good,
                "deleted_module_controls": mutations,
                "passed": not initial["passed"]
                and good["passed"]
                and all(not r["passed"] for r in mutations),
            }
            checks.append(row)
            print(ident, row["passed"], flush=True)
    save(output / "preflight.json", checks)
    if not all(row["passed"] for row in checks):
        raise ValueError("preflight_failed")


async def run(output, prepared):
    source = verify_source()
    output.mkdir(parents=True, exist_ok=False)
    checks = json.loads((prepared / "preflight.json").read_text())
    assert len(checks) == 12 and all(c["passed"] for c in checks)
    shutil.copytree(prepared / "graders", output / "graders")
    support.DATASETS = output / "graders"
    settings = config()
    protocol_hashes = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in [Path(__file__), HERE / "cases.py", HERE / "PROTOCOL.md"]
    }
    save(
        output / "freeze.json",
        {
            "source": source,
            "commit": SOURCE,
            "driver": protocol_hashes,
            "settings": settings.identity(),
            "endpoint_sha256": hashlib.sha256(settings.base_url.encode()).hexdigest(),
            "python": sys.executable,
            "import": aster.__file__,
            "time": datetime.now(UTC).isoformat(),
            "preflight": hashes(prepared),
            "limits": LIMITS,
        },
    )
    rows = []
    for index, c in enumerate(cases.worker_cases()):
        order = ["solo", "workers"] if index % 2 == 0 else ["workers", "solo"]
        for arm in order:
            name = f"worker-{c['id']}-{arm}"
            root = output / "workspaces" / name
            put_files(root, c["files"])
            row = await execute(
                name,
                root,
                config(max_workers=0 if arm == "solo" else 2),
                c["prompt"],
                output / "runs" / name,
                c["id"] + ".py",
            )
            row.update(family="worker", task=c["id"], category=c["category"], arm=arm)
            rows.append(row)
            save(output / "results.json", rows)
    for project_index, p in enumerate(cases.finish_projects()):
        name = "learn-" + p["id"]
        seed = output / "workspaces" / name
        put_files(seed, p["files"])
        learning_prompt = (
            f"Read this {p['topic']} project's conventions in README.md and docs/ to prepare "
            "for future coding sessions, then summarize the stable contracts. You may save useful durable "
            "project conventions with the supplied Memory tools so later sessions can reuse them. "
            "Saving is optional; choose the excerpts yourself. Do not implement or change any files."
        )
        learn = await execute(
            name,
            seed,
            config(memory_enabled=True, read_only=True),
            learning_prompt,
            output / "runs" / name,
            learning=True,
        )
        learn.update(family="learning", task=p["id"], arm="shared")
        rows.append(learn)
        save(output / "results.json", rows)
        arms = ["off", "basic", "maintained"]
        roots = {}
        for arm in arms:
            root = output / "workspaces" / f"memory-{p['id']}-{arm}"
            shutil.copytree(seed, root)
            roots[arm] = root
        for stage, task in enumerate(p["tasks"]):
            order = arms[(stage + project_index) % 3 :] + arms[: (stage + project_index) % 3]
            for arm in order:
                root = roots[arm]
                if stage == 2:
                    put_files(root, {"docs/" + k: v for k, v in p["drift"].items()})
                name = f"memory-{p['id']}-{stage}-{arm}"
                prompt = (
                    f"{task['path']}: {task['spec']}\nImplement in this Python repository using only the standard library. "
                    "Use project conventions already available to you; read sources when needed. Add or update "
                    "meaningful tests and run them via run_tests. Do not change README.md or docs/. "
                    "Keep all previous public functionality unless the new contract explicitly revises it."
                )
                row = await execute(
                    name,
                    root,
                    config(
                        memory_enabled=arm != "off",
                        memory_evolution=arm == "maintained",
                        auto_dream=arm == "maintained",
                    ),
                    prompt,
                    output / "runs" / name,
                    f"{p['id']}-{stage}.py",
                )
                row.update(family="memory", task=p["id"], stage=stage, arm=arm)
                rows.append(row)
                save(output / "results.json", rows)
    assert source == verify_source()
    assert protocol_hashes == {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in [Path(__file__), HERE / "cases.py", HERE / "PROTOCOL.md"]
    }
    summarize(output)


def summarize(output):
    rows = json.loads((output / "results.json").read_text())
    result = {}
    for family, arms in [
        ("worker", ["solo", "workers"]),
        ("memory", ["off", "basic", "maintained"]),
        ("learning", ["shared"]),
    ]:
        for arm in arms:
            data = [r for r in rows if r["family"] == family and r["arm"] == arm]
            result[f"{family}/{arm}"] = {
                "runs": len(data),
                "passed": sum(r["passed"] for r in data),
                "code_passed": sum(bool(r["grade"] and r["grade"]["passed"]) for r in data),
                "total_tokens": sum(r["usage"].get("total_tokens", 0) for r in data),
                "input_tokens": sum(r["usage"].get("input_tokens", 0) for r in data),
                "seconds": sum(r["seconds"] for r in data),
                "median_seconds": statistics.median(r["seconds"] for r in data) if data else None,
                **{
                    k: sum(r[k] for r in data)
                    for k in [
                        "model_calls",
                        "http_attempts",
                        "http_with_total_usage",
                        "doc_read_calls",
                        "read_calls",
                        "workers_started",
                        "workers_completed",
                        "coordination_calls",
                        "memory_store_attempts",
                        "memory_store_successes",
                    ]
                },
            }
    save(output / "summary.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["preflight", "run", "summarize"])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--prepared", type=Path)
    args = parser.parse_args()
    if args.mode == "preflight":
        asyncio.run(preflight(args.output.resolve()))
    elif args.mode == "run":
        asyncio.run(run(args.output.resolve(), args.prepared.resolve()))
    else:
        summarize(args.output.resolve())
