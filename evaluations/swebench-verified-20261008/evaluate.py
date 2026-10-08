"""One attempt per frozen SWE-bench instance, using Aster and original hidden tests."""
from __future__ import annotations

import argparse
import asyncio
import gzip
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
PRIVATE = REPO / "artifacts/private" / HERE.name
ENVIRONMENT = PRIVATE / "environment"
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(HERE.parent / "worker-code-20260928"))

import execution_environment  # noqa: E402
import repository_adapter as grader  # noqa: E402

from aster.config import load_settings  # noqa: E402
from aster.core.runtime import Runtime  # noqa: E402
from aster.schema import TaskSpec  # noqa: E402

grader.PRIVATE = PRIVATE
grader.DEPENDENCY_ROOT = ENVIRONMENT
original_parse = grader._parse


def environment(task):
    return PRIVATE / "xarray-runtime" if task["origin"]["repo"] == "pydata/xarray" else ENVIRONMENT


grader._runtime = environment


def parse(log, row):
    # The pinned upstream parser declares parse_log_xarray = parse_log_pytest.
    if row["log_parser"] == "parse_log_xarray":
        row = dict(row, log_parser="parse_log_pytest")
    if row["repo"] == "django/django":
        # unittest subtests can concatenate the next case and omit a parent status.
        log = re.sub(r"(?<= \.\.\. )(?=test\S+ \()", "\n", log)
        log += "\n" + "\n".join(
            f"{name} ... {status}"
            for status, name in re.findall(r"^(FAIL|ERROR): (test\S+ \([^\n)]+\))", log, re.M)
        )
    return original_parse(log, row)


grader._parse = parse


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".new")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def source_hashes():
    return {str(p.relative_to(REPO)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted((REPO / "src/aster").rglob("*.py"))}


def prepare(task, workspace):
    grader._row(task)
    grader._base(task, PRIVATE / "objects", workspace)
    commands = {
        "django/django": "PYTHONPATH=. python tests/runtests.py --settings=test_sqlite --parallel 1 <public test label>",
        "sphinx-doc/sphinx": "PYTHONPATH=. python -m pytest <public test path>",
        "sympy/sympy": "PYTHONPATH=. python bin/test -C --verbose <public test path>",
        "pydata/xarray": "PYTHONPATH=. python -m pytest <public test path>",
    }
    return {
        "test_python": str(environment(task) / "bin/python"), "dependency_root": str(environment(task)),
        "execution_env": {"PYTHONPATH": "{workspace}", "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1"},
        "prompt_suffix": "\n\nThe repository is at the issue's base revision. Dependencies are installed in the preconfigured Python environment; execution is offline. "
                         "Inspect the public tests to choose relevant checks. Test command format: " + commands[task["origin"]["repo"]]
                         + ". Implement the issue, verify your changes, and summarize the result.",
    }


def preflight(task):
    out = PRIVATE / "preflight" / task["id"]
    if (out / "preflight.json").exists():
        return json.loads((out / "preflight.json").read_text())
    root = out / "starter-workspace"
    prepare(task, root)
    start = time.monotonic()
    starter = grader.grade(task, PRIVATE / "objects", root, out / "starter")
    grader._patch(root, grader._row(task)["patch"])
    reference = grader.grade(task, PRIVATE / "objects", root, out / "reference")
    ready = (not starter["passed"] and not starter["missing_tests"] and reference["passed"]
             and any(value in {"FAILED", "ERROR"} for value in starter["fail_to_pass"].values()))
    result = {"instance_id": task["id"], "ready": ready, "starter": starter, "reference": reference,
              "seconds": round(time.monotonic() - start, 3)}
    save(out / "preflight.json", result)
    save(HERE / "preflight" / (task["id"] + ".json"), result)
    print(json.dumps({"preflight": task["id"], "ready": ready,
                      "starter_missing": len(starter["missing_tests"]),
                      "reference_missing": len(reference["missing_tests"]),
                      "reference_passed": reference["passed"]}), flush=True)
    return result


async def episode(task):
    out = PRIVATE / "runs" / task["id"]
    if (out / "result.json").exists():
        return json.loads((out / "result.json").read_text())
    assert json.loads((PRIVATE / "preflight" / task["id"] / "preflight.json").read_text())["ready"]
    assert source_hashes() == json.loads((HERE / "manifest.json").read_text())["source_hashes"], "production source changed"
    assert not out.exists(), "partial attempt exists; inspect before continuing"
    out.mkdir(parents=True)
    root = PRIVATE / "workspaces" / task["id"]
    metadata = prepare(task, root)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    execution_environment.install(metadata)
    settings = load_settings(REPO / ".env", context_tokens=64000, output_tokens=8192,
                             request_timeout=120, approval="auto", sandbox="required",
                             network=False, completion="strict", max_workers=0,
                             memory_enabled=True, memory_evolution=True, auto_dream=True)
    settings.stream = settings.protocol == "responses"
    prompt = task["prompt"] + metadata["prompt_suffix"]
    hashes = source_hashes()
    launch = {"instance_id": task["id"], "source_hashes": hashes, "settings": settings.identity(),
              "endpoint_sha256": hashlib.sha256(settings.base_url.encode()).hexdigest(),
              "prompt": prompt, "metadata": metadata, "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    save(out / "launch.json", launch)
    runtime = Runtime(root, settings)

    def progress(row):
        if row["event"] == "tool_result":
            print(json.dumps({"task": task["id"], "tool": row["call"]["name"],
                              "status": row["outcome"]["status"]}), flush=True)

    runtime.evidence.listeners.append(progress)
    started, error = time.monotonic(), None
    try:
        await asyncio.wait_for(runtime.run(TaskSpec(prompt=prompt, max_steps=90, max_turns=60,
                                                   max_tokens=1000000, require_edit=True,
                                                   require_verification=True)), 900)
    except Exception as exc:
        error = type(exc).__name__
    finally:
        foreground_seconds = round(time.monotonic() - started, 3)
        try:
            await runtime.maintenance.drain(timeout=15)
        finally:
            await runtime.maintenance.close()
            await runtime.workers.close()
            runtime.sandbox.close()
    state = runtime.state
    events = runtime.evidence.events(state.run_id)
    attempts = [r for r in events if r["event"] in {"provider_attempt", "memory_provider"}
                and r.get("type") == "diagnostic"]
    patch = subprocess.check_output(["git", "diff", "--binary"], cwd=root)
    (out / "prediction.patch").write_bytes(patch)
    # Include newly created product files in the export without staging private runtime state.
    new = subprocess.check_output(["git", "ls-files", "--others", "--exclude-standard", "-z"], cwd=root).split(b"\0")
    for path in new:
        if path and not path.startswith(b".aster/"):
            exported = subprocess.run(["git", "diff", "--no-index", "--binary", "--", "/dev/null", path.decode()],
                                      cwd=root, capture_output=True)
            patch += exported.stdout
    (out / "prediction.patch").write_bytes(patch)
    result = {"instance_id": task["id"], "difficulty": task["difficulty"], "repo": task["origin"]["repo"],
              "phase": state.phase, "stop_reason": state.stop_reason, "error": error, "final": state.final,
              "foreground_seconds": foreground_seconds, "seconds": round(time.monotonic() - started, 3),
              "usage": state.usage, "memory_usage": state.memory_usage, "team_usage": state.team_usage,
              "model_turns": state.turn, "provider_attempts": len(attempts),
              "attempts_with_total_usage": sum("total_tokens" in r.get("usage", {}) for r in attempts),
              "tool_calls": sum(r["event"] == "tool_result" for r in events),
              "source_unchanged": hashes == source_hashes(), "patch_sha256": hashlib.sha256(patch).hexdigest()}
    save(out / "agent-result.json", result)
    grade_started = time.monotonic()
    result["grade"] = grader.grade(task, PRIVATE / "objects", root, out / "grading")
    result["grading_seconds"] = round(time.monotonic() - grade_started, 3)
    result["resolved"] = result["grade"]["passed"]
    safe = runtime.evidence.redactor.value(result)
    save(out / "result.json", safe)
    save(HERE / "results" / (task["id"] + ".json"), safe)
    target = HERE / "predictions" / (task["id"] + ".patch")
    target.parent.mkdir(exist_ok=True)
    target.write_text(runtime.evidence.redactor.text(patch.decode()))
    trace = runtime.evidence.path("runs", state.run_id, "trace.jsonl")
    (target.parent / (task["id"] + ".trace.jsonl.gz")).write_bytes(gzip.compress(trace.read_bytes(), mtime=0))
    print(json.dumps({"completed": task["id"], "resolved": result["resolved"], "phase": state.phase,
                      "tokens": state.usage.get("total_tokens")}), flush=True)
    return safe


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["preflight", "run"])
    parser.add_argument("instance")
    args = parser.parse_args()
    task = next(t for t in json.loads((HERE / "tasks.json").read_text()) if t["id"] == args.instance)
    os.environ.update(OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1")
    if args.mode == "preflight":
        raise SystemExit(0 if preflight(task)["ready"] else 1)
    asyncio.run(episode(task))


if __name__ == "__main__":
    main()
