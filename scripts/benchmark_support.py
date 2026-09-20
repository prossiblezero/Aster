"""Dataset loading, independent grading, and Runtime-based evaluation episodes."""

from __future__ import annotations

import ast
import copy
import json
import re
import shutil
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from aster.config import Settings
from aster.core.runtime import Runtime
from aster.safety.runner import SandboxRunner
from aster.schema import ModelReply, TaskSpec, ToolCall
from aster.storage.workspace import Workspace
from aster.tools.testing import parse_junit

DATASETS = Path(__file__).resolve().parents[1] / "datasets"
USAGE_FIELDS = (
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
    "uncached_input_tokens",
    "reasoning_tokens",
)


def usage_metrics(usage, model_requests=None):
    """Retain reported subtotals and coverage without treating missing usage as zero."""
    counts = {name: usage.get(name) for name in USAGE_FIELDS}
    # Old checkpoints did not record coverage. Preserve their input/output fallback,
    # but never add independently partial input and output subtotals from new runs.
    if (
        counts["total_tokens"] is None
        and "usage_requests" not in usage
        and counts["input_tokens"] is not None
        and counts["output_tokens"] is not None
    ):
        counts["total_tokens"] = counts["input_tokens"] + counts["output_tokens"]
    coverage = {}
    for name in USAGE_FIELDS:
        reported = usage.get(
            f"{name}_requests", 0 if "usage_requests" in usage or counts[name] is None else None
        )
        coverage[name] = {
            "reported_requests": reported,
            "model_requests": model_requests,
            "complete": model_requests is not None
            and model_requests > 0
            and counts[name] is not None
            and reported == model_requests,
        }
    return {
        **counts,
        "usage": dict(usage),
        "model_requests": model_requests,
        "usage_coverage": coverage,
    }


def usage_summary(rows):
    """Keep historical reports readable; their absent coverage remains unknown."""
    summary = {}
    for name in USAGE_FIELDS:
        values = [row[name] for row in rows if row.get(name) is not None]
        coverage = [(row.get("usage_coverage") or {}).get(name, {}) for row in rows]
        item = {
            "reported_tokens": sum(values) if values else None,
            "reported_runs": len(values),
            "runs": len(rows),
            "complete": bool(rows) and all(c.get("complete", False) for c in coverage),
        }
        for field in ("reported_requests", "model_requests"):
            counts = [c.get(field) for c in coverage]
            item[field] = (
                sum(counts) if counts and all(value is not None for value in counts) else None
            )
        summary[name] = item
    return summary


def mean_tokens(rows):
    values = [row.get("total_tokens") for row in rows]
    return sum(values) / len(values) if values and all(v is not None for v in values) else None


def token_number(value):
    return "—" if value is None else f"{value:,.0f}"


def usage_label(summary):
    item = summary["total_tokens"]
    reported, requests = item["reported_requests"], item["model_requests"]
    coverage = f"{reported}/{requests}" if reported is not None and requests is not None else "未知"
    return f"{token_number(item['reported_tokens'])}（覆盖 {coverage}）"


def dataset_path(name):
    path = (DATASETS / name).resolve()
    if not path.is_relative_to(DATASETS) or not path.exists():
        raise ValueError(f"invalid dataset path: {name}")
    return path


def load_bank():
    tasks = json.loads(dataset_path("benchmark_tasks.json").read_text())["tasks"]
    ids = [t["id"] for t in tasks]
    if not tasks or len(set(ids)) != len(ids):
        raise ValueError("empty task bank or duplicate IDs")
    for task in tasks:
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", task["id"]):
            raise ValueError("invalid task ID")
        if task["suite"] not in {"harness", "repository", "function", "memory"}:
            raise ValueError("unknown suite")
        if not task["prompt"] or not task["checks"]:
            raise ValueError("task needs prompt and grader")
        dataset_path(task["fixture"])
        if task["checks"].get("verifier"):
            dataset_path(task["checks"]["verifier"])
        if task["suite"] != "memory" and not task.get("actions"):
            raise ValueError("non-memory task needs reference actions")
    return tasks


def prepare(task, root):
    shutil.copytree(dataset_path(task["fixture"]), root)
    for name, content in task.get("files", {}).items():
        path = Workspace(root).path(name, write=True)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


def task_spec(task):
    return TaskSpec(
        prompt=task["prompt"],
        task_id=task["id"],
        max_steps=task["max_steps"],
        max_turns=task["max_turns"],
        require_edit=task.get("require_edit", False),
        require_verification=task.get("require_verification", False),
    )


def model_reply(action):
    if "final" in action:
        answer = action["final"]
        return ModelReply(text=answer, raw=[{"role": "assistant", "content": answer}])
    call = ToolCall.model_validate(action)
    return ModelReply(
        calls=[call],
        raw=[
            {
                "type": "function_call",
                "call_id": call.call_id,
                "name": call.name,
                "arguments": json.dumps(call.arguments),
            }
        ],
    )


class ScriptedPolicy:
    """Reference tool actions test the real loop, not the model's coding ability."""

    def __init__(self, actions):
        self.actions = iter(copy.deepcopy(actions))
        self.runtime = None

    async def complete(self, _messages, _tools):
        try:
            action = next(self.actions)
        except StopIteration as exc:
            raise RuntimeError("reference_actions_exhausted") from exc
        args = action.get("arguments", {})
        if args.get("evidence_id") == "$last_evidence":
            events = self.runtime.evidence.events(self.runtime.state.run_id)
            args["evidence_id"] = next(
                e["hash"] for e in reversed(events) if e["event"] == "tool_decision"
            )
        return model_reply(action)


class MemoryPolicy:
    """Answers only from received observations. No expected-answer parameter."""

    def __init__(self, source_path):
        self.source_path = source_path
        self.requested = False

    async def complete(self, messages, _tools):
        text = json.dumps(messages, ensure_ascii=False)
        match = re.search(r'FACT: ([^\\\n"\r]+)', text)
        if match:
            fact = match[1].split(" (evidence:", 1)[0].strip()
            value = fact.split(" is ", 1)[-1].removesuffix(" seconds")
            return model_reply({"final": json.dumps({"value": value})})
        if not self.requested:
            self.requested = True
            return model_reply({"name": "read_file", "arguments": {"path": self.source_path}})
        return model_reply({"final": "No supported fact was observed."})


async def external_check(root, verifier, selection=None):
    """Grade selected hidden tests from JUnit results, never text in candidate stdout.

    Candidate code still shares the pytest process. This rejects accidental false
    greens, skip/exit tricks and workspace edits; it is not an adversarial Python VM.
    """
    checks = dataset_path(verifier)
    expected = {
        node.name
        for node in ast.parse(checks.read_text()).body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and node.name.startswith("test_")
    }
    if selection:
        if selection not in expected:
            raise ValueError(f"unknown hidden test selection: {selection}")
        expected = {selection}
    if not expected:
        raise ValueError("hidden grader must declare at least one test")
    with tempfile.TemporaryDirectory(prefix="aster-grader-") as directory:
        target = Path(directory)
        workspace = Workspace(root)
        for source in workspace.files():
            dest = target / source.relative_to(workspace.root)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, dest)
        shutil.copy2(checks, target / "__evaluation_tests.py")
        (target / "__evaluation_pytest.ini").write_text("[pytest]\n")
        # A unique absent path rules out accepting a stale report copied from the candidate.
        report_name = "__evaluation_result_" + Path(directory).name + ".xml"
        report = target / report_name
        command = (
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -I -m pytest -q -p no:cacheprovider "
            f"--junitxml={report_name} -c __evaluation_pytest.ini "
            "--confcutdir=. --noconftest __evaluation_tests.py"
        )
        if selection:
            command += "::" + selection
        result = await SandboxRunner(
            Settings(sandbox="required", network=False, write_scope=[report_name])
        ).run(
            Workspace(target),
            command,
            timeout=30,
        )
        cases = []
        try:
            cases = parse_junit(Workspace(target).read(report.name))
        except (OSError, ValueError, ET.ParseError):
            pass
        passed = [case for case in cases if case["status"] == "passed"]
        complete = (
            len(cases) == len(expected)
            and {case["name"] for case in cases} == expected
            and all(case["classname"] == "__evaluation_tests" for case in cases)
        )
        verified = complete and len(passed) == len(expected)
        return {
            "passed": result.status == "success" and verified,
            "exit_code": result.exit_code,
            "status": result.status,
            "output": result.content,
            "isolation": result.data.get("isolation"),
            "tests": {"expected": len(expected), "executed": len(cases), "passed": len(passed)},
            "grader_reason": (
                result.reason or "test_process_failed"
                if result.status != "success"
                else "all_tests_passed"
                if verified
                else "hidden_tests_incomplete_or_failed"
            ),
        }


def tool_events(runtime):
    return [e for e in runtime.evidence.events(runtime.state.run_id) if e["event"] == "tool_result"]


def metrics(runtime, seconds, *, model_requests=None):
    state = runtime.state
    events = tool_events(runtime)
    signatures = [
        (e["call"]["name"], json.dumps(e["call"]["arguments"], sort_keys=True)) for e in events
    ]
    tools = {}
    for event in events:
        name, outcome = event["call"]["name"], event["outcome"]
        counts = tools.setdefault(name, {"calls": 0, "success": 0, "reasons": {}})
        counts["calls"] += 1
        counts["success"] += outcome["status"] == "success"
        if outcome["status"] != "success":
            reason = outcome["reason"] or outcome["status"]
            counts["reasons"][reason] = counts["reasons"].get(reason, 0) + 1
    return {
        "tools": tools,
        "steps": state.step,
        "turns": state.turn,
        "read_calls": sum(e["call"]["name"] == "read_file" for e in events),
        "repeated_calls": len(signatures) - len(set(signatures)),
        "failed_calls": sum(e["outcome"]["status"] != "success" for e in events),
        **usage_metrics(state.usage, state.turn if model_requests is None else model_requests),
        "seconds": round(seconds, 3),
    }


def archive(runtime, destination):
    state = runtime.state
    shutil.copytree(
        runtime.evidence.path("runs", state.run_id), destination / "run", dirs_exist_ok=True
    )
    (destination / "final.txt").write_text(state.final)
    for source in runtime.workspace.files():
        target = destination / "candidate" / source.relative_to(runtime.workspace.root)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    return str(destination / "run" / "trace.jsonl")


def grade_contract(task, runtime, original, setup):
    expected = task["checks"]
    state = runtime.state
    events = runtime.evidence.events(state.run_id)
    checks = {"phase": state.phase == expected.get("phase", "completed")}
    for kind in ("contains", "absent"):
        for path, texts in expected.get(kind, {}).items():
            candidate = runtime.workspace.path(path)
            content = candidate.read_text() if candidate.is_file() else ""
            checks[f"{kind}:{path}"] = candidate.is_file() and all(
                (text in content) == (kind == "contains") for text in texts
            )
    if "answer_contains" in expected:
        checks["answer"] = expected["answer_contains"].casefold() in state.final.casefold()
    if "answer_value" in expected:
        try:
            answer = json.loads(
                state.final.strip().removeprefix("```json").removesuffix("```").strip()
            )
        except ValueError:
            answer = None
        checks["answer"] = (
            isinstance(answer, dict)
            and str(answer.get("value", "")).casefold() == expected["answer_value"].casefold()
        )
    if expected.get("unchanged"):
        checks["unchanged"] = original == runtime.workspace.snapshot()
    for name in expected.get("events", []):
        checks["event:" + name] = any(e["event"] == name for e in events)
    for reason in expected.get("outcome_reasons", []):
        checks["outcome:" + reason] = any(
            e["outcome"]["reason"] == reason for e in tool_events(runtime)
        )
    lessons = [x["text"] for x in runtime.memory.state["lessons"]]
    for kind in ("memory_contains", "memory_excludes"):
        for text in expected.get(kind, []):
            checks[kind + ":" + text] = (text in lessons) == (kind == "memory_contains")
    if expected.get("compacted"):
        checks["compacted"] = any(
            e["event"] == "context_built" and e.get("compacted") for e in events
        )
    if expected.get("checkpoint"):
        cp = json.loads(runtime.evidence.path("runs", state.run_id, "checkpoint.json").read_text())
        checks["checkpoint"] = cp["state"]["run_id"] == state.run_id
    if expected.get("resume_rejected"):
        checks["resume_rejected"] = setup.get("resume_rejected") == expected["resume_rejected"]
    if expected.get("working_contains"):
        checks["working_contains"] = expected["working_contains"] in json.dumps(
            state.working_memory
        )
    return checks


async def apply_setup(task, runtime):
    setup = task.get("setup", {})
    observed = {}
    if setup.get("kind") == "context_pressure":
        runtime.state.messages.extend(
            {"role": "tool", "content": setup["message_text"]}
            for _ in range(setup["message_count"])
        )
    if setup.get("kind") in {"resume_drift", "resume_identity"}:
        if setup["kind"] == "resume_drift":
            await runtime.step(ToolCall(name="read_file", arguments={"path": setup["path"]}))
        runtime.checkpoint()
        settings = runtime.settings
        if setup["kind"] == "resume_drift":
            runtime.workspace.path(setup["path"]).write_text(setup["changed_text"])
        else:
            settings = settings.model_copy(update={"approval": setup["approval"]})
        try:
            Runtime(runtime.workspace.root, settings).resume(runtime.state.run_id)
        except ValueError as exc:
            observed["resume_rejected"] = str(exc)
        # Rejected resume never silently continues: explicitly start a new run and re-read.
        runtime.finish("benchmark_restart_after_rejection")
        runtime.start(task_spec(task))
        runtime.evidence.emit(runtime.state.run_id, "benchmark_resume_check", **observed)
    return observed


async def run_task(task, root, destination, settings, *, real=False, variant="core"):
    prepare(task, root)
    settings = settings.model_copy(update={**task.get("settings", {}), "tool_profile": variant})
    policy = None if real else ScriptedPolicy(task["actions"])
    runtime = Runtime(root, settings, model=policy)
    if policy:
        policy.runtime = runtime
    original = runtime.workspace.snapshot()
    original_text = {name: runtime.workspace.read(name) for name in original}
    runtime.start(task_spec(task))
    setup = await apply_setup(task, runtime)
    started = time.monotonic()
    error = ""
    try:
        await runtime.run()
    except Exception as exc:
        error = runtime.evidence.redactor.text(f"{type(exc).__name__}: {exc}")
        runtime.finish("evaluation_exception", failed=True)
    seconds = time.monotonic() - started
    checks = grade_contract(task, runtime, original, setup)
    if error:
        checks["runtime_error"] = False
    verifier = task["checks"].get("verifier")
    independent = await external_check(root, verifier) if verifier else None
    if independent:
        checks["independent_tests"] = independent["passed"]
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "patch.diff").write_text(runtime.workspace.diff(original_text))
    row = {
        "task_id": task["id"],
        "suite": task["suite"],
        "variant": variant,
        "mode": "real" if real else "scripted",
        "passed": bool(checks) and all(checks.values()),
        "checks": checks,
        "failure": error or ", ".join(k for k, v in checks.items() if not v),
        "stop_reason": runtime.state.stop_reason,
        "verifier": independent,
        **metrics(runtime, seconds),
        "trace_path": archive(runtime, destination),
    }
    runtime.evidence.save(("evaluation.json",), row)
    (destination / "result.json").write_text(json.dumps(row, ensure_ascii=False, indent=2) + "\n")
    return row


async def run_memory(task, root, destination, settings, *, real=False, enabled=True):
    prepare(task, root)
    # Both arms experience the same acquisition. Only the fresh follow-up's recall differs.
    acquisition = Runtime(
        root, settings.model_copy(update={"memory_enabled": True, "auto_dream": False})
    )
    acquisition.start(TaskSpec(prompt="Read and remember the documented project fact."))
    observation = await acquisition.step(
        ToolCall(name="read_file", arguments={"path": task["source_path"]})
    )
    stored = await acquisition.step(
        ToolCall(
            name="memory_store",
            arguments={
                "text": observation.content.strip(),
                "evidence_id": observation.evidence_id,
                "tags": task["query"].split(),
            },
        )
    )
    acquisition.finalize("Recorded the observed fact.")
    destination.mkdir(parents=True, exist_ok=True)
    archive(acquisition, destination / "acquisition")
    runtime = Runtime(
        root,
        settings.model_copy(update={"memory_enabled": enabled, "read_only": True}),
        model=None if real else MemoryPolicy(task["source_path"]),
    )
    original = runtime.workspace.snapshot()
    runtime.start(task_spec(task))
    fresh = not runtime.state.messages and not runtime.state.working_memory
    started = time.monotonic()
    error = ""
    try:
        await runtime.run()
    except Exception as exc:
        error = runtime.evidence.redactor.text(f"{type(exc).__name__}: {exc}")
        runtime.finish("evaluation_exception", failed=True)
    checks = grade_contract(task, runtime, original, {})
    checks.update(acquisition_stored=stored.status == "success", fresh_followup=fresh)
    if error:
        checks["runtime_error"] = False
    row = {
        "task_id": task["id"],
        "suite": "memory",
        "variant": "memory_on" if enabled else "memory_off",
        "mode": "real" if real else "scripted",
        "passed": all(checks.values()),
        "checks": checks,
        "failure": error or ", ".join(k for k, v in checks.items() if not v),
        "stop_reason": runtime.state.stop_reason,
        **metrics(runtime, time.monotonic() - started),
        "acquisition_steps": acquisition.state.step,
        "trace_path": archive(runtime, destination),
    }
    (destination / "result.json").write_text(json.dumps(row, ensure_ascii=False, indent=2) + "\n")
    return row
