"""Execute fixed mechanism scenarios; task content and expected outcomes stay in datasets."""

from __future__ import annotations

import copy
import shutil
from pathlib import Path

from aster.config import Settings
from aster.core.hooks import HookResult, Hooks
from aster.knowledge.memory import Memory
from aster.runtime import Runtime
from aster.schema import Final, TaskSpec, ToolCall, VerifierResult
from aster.storage.workspace import Workspace, digest
from aster.training.env import AsterEnv


def memory_case(case: dict, root: Path, *, evolution: bool) -> dict:
    memory = Memory(root, evolution=evolution)
    checks = {}
    observed = None
    kind = case["kind"]
    if kind == "admission":
        item = memory.record(
            case["text"],
            run_id=case["id"],
            evidence_id=case.get("evidence_id", "observation"),
            observation=case["observation"],
            tags=case.get("tags", []),
        )
        checks["admission"] = bool(item) == case["expected_admitted"]
        checks["reason"] = memory.last_rejection == case.get("expected_reason", "")
        checks["rejected_content_absent"] = (
            bool(item) or case["text"] not in memory.path.read_text()
        )
        observed = bool(item)
    elif kind == "feedback":
        for i, lesson in enumerate(case["lessons"]):
            item = memory.record(
                lesson["text"], run_id=case["id"], evidence_id=f"e{i}", observation=lesson["text"]
            )
            checks[f"admitted_{i}"] = bool(item)
            if item:
                for success in lesson["uses"]:
                    memory.record_use([item["id"]], success)
        if case["consolidate"]:
            memory.dream()
        recalled = memory.retrieve(case["query"], limit=1)
        observed = recalled[0]["text"] if recalled else None
        checks["expected_variant_recall"] = (
            observed == case["expected_on" if evolution else "expected_off"]
        )
    else:
        source = root / "conventions.txt"
        text = case.get("source", case.get("text"))
        source.write_text(text)
        item = memory.record(
            text.strip(),
            run_id=case["id"],
            evidence_id="observation",
            observation=text,
            sources={source.name: digest(text)},
        )
        checks["admitted"] = bool(item)
        snapshot = memory.snapshot()
        baseline = copy.deepcopy(memory.state["snapshots"][snapshot])
        mutation = case["mutation"]
        if mutation in {"replace", "source"}:
            source.write_text(case["replacement"])
        elif mutation in {"delete", "symlink"}:
            source.unlink()
            if mutation == "symlink":
                target = root / "other.txt"
                target.write_text(text)
                source.symlink_to(target)
        elif mutation == "feedback" and item:
            memory.record_use([item["id"]], False)
            memory.record_use([item["id"]], False)
            memory.dream()
            checks["first_rollback"] = memory.rollback(snapshot)
            memory.record_use([item["id"]], True)
        if kind == "rollback":
            checks["rollback"] = memory.rollback(snapshot)
            checks["state_restored"] = memory.state["lessons"] == baseline["lessons"]
            checks["snapshot_unchanged"] = memory.state["snapshots"][snapshot] == baseline
        observed = bool(memory.retrieve(case["query"]))
        checks["freshness"] = observed == case["expected_recalled"]
    return {
        "case_id": case["id"],
        "suite": f"memory_{kind}",
        "variant": "evolution_on" if evolution else "evolution_off",
        "contract_passed": all(checks.values()),
        "checks": checks,
        "goal_passed": observed == case["expected_on"]
        if kind == "feedback"
        else all(checks.values()),
        "observed": observed,
        "effect": case.get("effect", "contract"),
        "memory_state": copy.deepcopy(memory.state),
    }


class ScenarioProbe:
    def __init__(self, raise_before=False):
        self.raise_before = raise_before
        self.stop_calls = 0

    def before_tool(self, *_):
        if self.raise_before:
            raise RuntimeError("scenario_precondition_failed")
        return HookResult()

    def stop(self, *_):
        self.stop_calls += 1
        return HookResult()


class BeforeHookAblation(Hooks):
    """Disable only the tested boundary; permissions and sandbox remain active."""

    def before_tool(self, *_):
        return HookResult()


def file_contents(root: Path, expected: dict) -> dict:
    return {
        name: (root / name).read_text() if (root / name).is_file() else None for name in expected
    }


def check_expected(observed: dict, expected: dict) -> dict:
    return {
        key: all(text in observed.get("diff", "") for text in wanted)
        if key == "diff_contains"
        else observed.get(key, "").startswith(wanted + ":")
        if key == "last_error" and wanted == "runtime_identity_mismatch"
        else observed.get(key) == wanted
        for key, wanted in expected.items()
    }


async def runtime_case(case: dict, root: Path, variant: str) -> dict:
    arm = case["variants"][variant]
    settings = Settings(**(case["settings"] | arm.get("settings", {})))
    probe = ScenarioProbe(case.get("hook_error", False))
    hooks = (Hooks if arm.get("before_hooks", True) else BeforeHookAblation)([probe])
    runtime = Runtime(root, settings, hooks=hooks)
    runtime.start(TaskSpec(**case["task"]))
    outcomes, last_error, diff = [], "", ""
    for operation in case["operations"]:
        if "tool" in operation:
            outcome = await runtime.step(ToolCall.model_validate(operation["tool"]))
            outcomes.append(outcome.model_dump(exclude={"data"}))
            if operation["tool"]["name"] == "git_diff":
                diff = outcome.content
        elif "final" in operation:
            runtime.finalize(operation["final"])
        elif "finish" in operation:
            runtime.finish(**operation["finish"])
        elif "restart" in operation:
            runtime.start(TaskSpec(**(case["task"] | operation["restart"])))
        elif "resume" in operation:
            args = operation["resume"]
            for name, text in args.get("files", {}).items():
                runtime.workspace.path(name, write=True).write_text(text)
            resumed = Runtime(
                root, settings.model_copy(update=args.get("settings", {})), hooks=hooks
            )
            try:
                resumed.resume(runtime.state.run_id)
            except ValueError as exc:
                last_error = str(exc)
            else:
                runtime = resumed
        else:
            raise ValueError(f"unknown operation: {operation.keys()}")
    expected = arm["expected"]
    observed = {
        "phase": runtime.state.phase,
        "stop_reason": runtime.state.stop_reason,
        "recovery_used": runtime._recovery_used,
        "last_error": last_error,
        "stop_calls": probe.stop_calls,
        "blocked": sum(o["status"] == "blocked" for o in outcomes),
        "diff": diff,
        "files": file_contents(root, expected.get("files", {}) | case["goal"].get("files", {})),
    }
    checks = check_expected(observed, expected)
    goals = check_expected(observed, case["goal"])
    runtime.checkpoint()
    await runtime.workers.close()
    return {
        "case_id": case["id"],
        "suite": case["suite"],
        "variant": variant,
        "contract_passed": bool(checks) and all(checks.values()),
        "checks": checks,
        "goal_passed": bool(goals) and all(goals.values()),
        "goal_checks": goals,
        "observed": observed,
        "outcomes": outcomes,
    }


async def rl_case(case: dict, root: Path, variant: str) -> dict:
    def verifier(workspace, _state):
        expected = case["reward_files"]
        return VerifierResult(
            passed=file_contents(workspace, expected) == expected,
            reason="independent_fixture_comparison",
        )

    env = AsterEnv(
        root, Settings(**case["settings"]), verifier=verifier if case.get("reward_files") else None
    )
    _, reset_info = env.reset(TaskSpec(**case["task"]))
    initial = Workspace(root).snapshot()
    transitions, observed = [], {"positive_tool_rewards": 0}
    try:
        for operation in case["operations"]:
            if "reset" in operation:
                shutil.copytree(env.runtime.evidence.root, root / ".aster", dirs_exist_ok=True)
                before = reset_info["run_id"]
                _, reset_info = env.reset(TaskSpec(**(case["task"] | operation["reset"])))
                observed["fresh_reset"] = (
                    before != reset_info["run_id"]
                    and env.runtime.workspace.snapshot() == initial
                    and not env.runtime.state.working_memory
                    and not env.runtime.state.messages
                )
                observed["seed_unchanged"] = Workspace(root).snapshot() == initial
                continue
            action = (
                Final(answer=operation["final"])
                if "final" in operation
                else ToolCall.model_validate(operation["tool"])
            )
            try:
                _, reward, terminated, truncated, info = await env.step(action)
            except RuntimeError as exc:
                observed["terminal_rejected"] = str(exc).startswith("episode_finished:")
                if not observed["terminal_rejected"]:
                    raise
                continue
            info["trace_path"] = f"state/runs/{info['run_id']}/trace.jsonl"
            transitions.append(
                {"reward": reward, "terminated": terminated, "truncated": truncated, "info": info}
            )
            observed.update(terminated=terminated, truncated=truncated)
            if isinstance(action, Final):
                observed["final_reward"] = reward
                observed["reward_verified"] = (info["reward_verification"] or {}).get("passed")
            elif reward > 0:
                observed["positive_tool_rewards"] += 1
        checks = check_expected(observed, case["variants"][variant]["expected"])
        goals = check_expected(observed, case["goal"])
        shutil.copytree(env.runtime.evidence.root, root / ".aster", dirs_exist_ok=True)
        return {
            "case_id": case["id"],
            "suite": case["suite"],
            "variant": variant,
            "contract_passed": bool(checks) and all(checks.values()),
            "checks": checks,
            "goal_passed": bool(goals) and all(goals.values()),
            "goal_checks": goals,
            "observed": observed,
            "transitions": transitions,
        }
    finally:
        await env.runtime.workers.close()
        env.close()


async def run_case(case: dict, root: Path, *, variant: str = "evolution_on") -> dict:
    root.mkdir(parents=True, exist_ok=True)
    for name, text in case.get("files", {}).items():
        path = Workspace(root).path(name, write=True)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    if case["kind"] in {"admission", "feedback", "freshness", "rollback"}:
        return memory_case(case, root, evolution=variant == "evolution_on")
    if case["kind"] == "runtime":
        return await runtime_case(case, root, variant)
    if case["kind"] == "rl":
        return await rl_case(case, root, variant)
    if case["kind"] == "tool_contract":
        runtime = Runtime(root, Settings(approval="auto"))
        runtime.start(TaskSpec(prompt="Inspect test results", require_observation=False))
        outcome = await runtime.step(
            ToolCall(name="run_tests", arguments={"command": case["command"]})
        )
        checks = {
            "status": outcome.status == case["status"],
            "reason": outcome.reason == case["reason"],
            "verification": (runtime.state.verification.state == "passed")
            == (case["status"] == "success"),
        }
        runtime.finish("contract_evaluated")
        return {
            "case_id": case["id"],
            "suite": "tool_contract",
            "variant": variant,
            "contract_passed": all(checks.values()),
            "goal_passed": all(checks.values()),
            "checks": checks,
            "outcome": outcome.model_dump(),
        }
    raise ValueError(f"unknown scenario kind: {case['kind']}")
