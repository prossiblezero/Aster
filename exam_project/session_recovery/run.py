"""Two authorized stages, one injected pre-HTTP fault, no candidate repair or rerun."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.util
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE_COMMIT = "d20c38fface3751fd366e303e77c81d6ce66c6f8"
SOURCE = HERE / ".source"
SHARED_PATH = HERE.parent / "stream_comparison/run.py"
SPEC = importlib.util.spec_from_file_location("session_recovery_shared", SHARED_PATH)
shared = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(shared)
MAX_STEPS, MAX_TURNS, WALL_SECONDS = 30, 16, 1800
RECOVERY_FEEDBACK = "继续剩余工作。"


def experiment_snapshot():
    paths = [
        Path(__file__),
        HERE / "requirements.md",
        HERE / "followup.md",
        HERE / "checks_stage1.py",
        HERE / "checks_stage2.py",
        HERE / "README.md",
        HERE / "test_driver.py",
        SHARED_PATH,
    ]
    return {
        str(path.relative_to(HERE.parent)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in paths
    }


def file_hashes(runtime, paths=None):
    paths = paths if paths is not None else runtime.workspace.snapshot()
    return {
        name: hashlib.sha256(runtime.workspace.path(name).read_bytes()).hexdigest()
        for name in paths
        if runtime.workspace.path(name).is_file()
    }


def remaining_budget(state, elapsed):
    steps, turns, seconds = MAX_STEPS - state.step, MAX_TURNS - state.turn, WALL_SECONDS - elapsed
    if min(steps, turns, seconds) <= 0:
        raise ValueError("shared stage budget exhausted; no recovery request allowed")
    return {"max_steps": steps, "max_turns": turns}, seconds


class FaultGate:
    def __init__(self):
        self.runtime = None
        self.first_write = None
        self.writes = []
        self.injection = None

    def before_tool(self, _state, call):
        from aster.hooks import HookResult

        return (
            HookResult("deny", "skills_disabled_for_experiment")
            if call.name == "skill_load"
            else HookResult()
        )

    def after_tool(self, state, call, outcome):
        if outcome.changed_paths and outcome.status == "success":
            entry = {
                "run_id": state.run_id,
                "call_id": call.call_id,
                "tool": call.name,
                "files": file_hashes(self.runtime, outcome.changed_paths),
            }
            self.writes.append(entry)
            if self.first_write is None:
                self.first_write = entry

    def inject_if_armed(self):
        from aster.provider import ProviderError

        if self.first_write is not None and self.injection is None:
            self.injection = {
                "simulated": True,
                "before_http": True,
                "http_attempts": 0,
                "run_id": self.runtime.state.run_id,
                "turn": self.runtime.state.turn,
                "files": file_hashes(self.runtime),
                "used_call_ids": list(self.runtime.state.used_call_ids),
                "trigger": self.first_write,
            }
            raise ProviderError(
                "SIMULATED_EXPERIMENT_FAILURE_AFTER_SUCCESSFUL_WRITE_BEFORE_HTTP",
                category="transport",
                code="experiment_injected",
                attempts=0,
            )


def observed_model(settings, gate=None):
    class ObservedModel(shared.timed_model_class()):
        async def complete(self, messages, tools, *, on_event=None):
            if gate is not None:
                gate.inject_if_armed()
            return await super().complete(messages, tools, on_event=on_event)

    return ObservedModel(settings)


async def grade(support, root, stage):
    previous = support.DATASETS
    support.DATASETS = HERE
    try:
        return await support.external_check(root, f"checks_stage{stage}.py")
    except Exception as exc:
        return {"passed": False, "status": "grader_error", "error": type(exc).__name__}
    finally:
        support.DATASETS = previous


def state_record(runtime, model, seconds):
    state = runtime.state
    return {
        "run_id": state.run_id,
        "session_id": state.session_id,
        "phase": state.phase,
        "stop_reason": state.stop_reason,
        "steps": state.step,
        "turns": state.turn,
        "seconds": seconds,
        "usage": dict(state.usage),
        "requests": model.requests,
        "used_call_ids": list(state.used_call_ids),
        "files": file_hashes(runtime),
        "verification": state.verification.model_dump(),
    }


async def bounded_run(runtime, model, seconds, task=None):
    start = time.monotonic()
    error = None
    try:
        await asyncio.wait_for(runtime.run(task), seconds)
    except (Exception, asyncio.CancelledError) as exc:
        error = type(exc).__name__
    record = state_record(runtime, model, time.monotonic() - start)
    if error:
        record["error"] = error
    return record


def make_runtime(root, settings, gate, model):
    from aster.hooks import Hooks
    from aster.runtime import Runtime

    runtime = Runtime(
        root, settings, model=model, hooks=Hooks([gate]), approval_callback=lambda *_: True
    )
    gate.runtime = runtime
    return runtime


async def stage_one(root, settings, task, persist):
    gate = FaultGate()
    first_model = observed_model(settings, gate)
    first = make_runtime(root, settings, gate, first_model)
    started = time.monotonic()
    before = await bounded_run(first, first_model, WALL_SECONDS, task)
    result = {"before_fault": before, "simulated_fault": gate.injection}
    persist("stage1-before-resume.json", result)
    restored = first
    try:
        if gate.injection is None or before["stop_reason"] != "provider_error":
            result["status"] = "required_injected_boundary_not_reached"
            return first, gate, result
        limits, _ = remaining_budget(first.state, time.monotonic() - started)
        model = observed_model(settings)
        restored = make_runtime(root, settings, gate, model)
        restored.resume(first.state.run_id)
        result["restore_checks"] = {
            "same_session": restored.state.session_id == first.state.session_id,
            "same_files": file_hashes(restored) == gate.injection["files"],
            "same_used_call_ids": restored.state.used_call_ids == gate.injection["used_call_ids"],
            "same_native_history": restored._structured_history == first._structured_history,
        }
        if not all(result["restore_checks"].values()):
            raise ValueError("restoration changed completed work or native history")
        restored.continue_task(RECOVERY_FEEDBACK, **limits)
        _, seconds = remaining_budget(first.state, time.monotonic() - started)
        result["recovery_budget"] = {**limits, "seconds": seconds}
        result["recovery_feedback"] = RECOVERY_FEEDBACK
        result["after_resume"] = await bounded_run(restored, model, seconds)
        result["status"] = restored.state.phase
    except (Exception, asyncio.CancelledError) as exc:
        result["status"] = "recovery_setup_failed"
        result["recovery_error"] = type(exc).__name__
        # The pre-fault record remains valid even when resume/setup cannot continue.
        if restored.state is None:
            restored = first
    finally:
        result["total_steps"] = before["steps"] + result.get("after_resume", {}).get("steps", 0)
        result["total_turns"] = before["turns"] + result.get("after_resume", {}).get("turns", 0)
        result["seconds"] = time.monotonic() - started
        result["budget_respected"] = (
            result["total_steps"] <= MAX_STEPS
            and result["total_turns"] <= MAX_TURNS
            and result["seconds"] <= WALL_SECONDS
            and not any(
                record.get("error") == "TimeoutError"
                for record in (before, result.get("after_resume", {}))
            )
        )
        if gate.injection:
            result["prior_success_ids_retained"] = set(gate.injection["used_call_ids"]) <= set(
                restored.state.used_call_ids
            )
            result["first_write_executed_once"] = (
                sum(row["call_id"] == gate.first_write["call_id"] for row in gate.writes) == 1
            )
        persist("stage1-runtime.json", result)
    return restored, gate, result


def settings_for_experiment(env_file):
    from aster.config import ModelProfile, load_settings

    settings = load_settings(
        env_file,
        model="gpt-5.6-sol",
        protocol="responses",
        stream=False,
        context_tokens=128000,
        output_tokens=32768,
        request_timeout=300,
        sandbox="required",
        approval="auto",
        network=False,
        memory_enabled=False,
        memory_evolution=False,
        auto_dream=False,
        max_workers=0,
        read_parallelism=1,
    )
    settings.profiles = {
        "compact-sol": ModelProfile(
            provider=settings.provider,
            model=settings.model,
            protocol=settings.protocol,
            base_url=settings.base_url,
            auth=settings.auth,
            api_key_env="ASTER_SESSION_EXPERIMENT_KEY" if settings.auth != "none" else None,
            context_tokens=64000,
            output_tokens=32768,
            stream=False,
        )
    }
    settings._profile_credentials = {"ASTER_SESSION_EXPERIMENT_KEY": settings.api_key}
    return settings


def totals(records, support):
    usage = {}
    requests = []
    for record in records:
        if not record:
            continue
        requests.extend(record["requests"])
        for key, value in record["usage"].items():
            usage[key] = usage.get(key, 0) + value
    return {
        "usage_metrics": support.usage_metrics(usage, len(requests)),
        "real_model_requests": len(requests),
        "http_attempts": sum(row["http_attempts"] for row in requests),
        "http_retries": sum(row["retries"] for row in requests),
        "real_request_errors": sum(row["status"] != "completed" for row in requests),
    }


def credential_scan(root, secret):
    scanned, leaks = 0, []
    for path in root.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        scanned += 1
        if secret and secret.encode() in path.read_bytes():
            leaks.append(str(path.relative_to(root)))
    return {"files_scanned": scanned, "plaintext_found": bool(leaks), "paths": leaks}


async def run_experiment(args, support, snapshot):
    from aster.evidence import Redactor
    from aster.schema import TaskSpec

    destination = HERE / "run"
    if destination.exists():
        raise ValueError("Refusing to repeat or overwrite the authorized attempt")
    settings = settings_for_experiment(args.env_file)
    secret = settings.api_key.get_secret_value()
    if settings.auth != "none" and not secret:
        raise ValueError("provider credential missing")
    redactor = Redactor([secret])
    root = destination / "candidate"
    root.mkdir(parents=True)
    evidence = destination / "evidence"

    def persist(name, value):
        shared.write_json(evidence / name, redactor.value(value, mask_fields=False))

    frozen_experiment = experiment_snapshot()
    launch = {
        "source_commit": SOURCE_COMMIT,
        "source": snapshot,
        "configuration": settings.identity(),
        "stage_limits": {"steps": MAX_STEPS, "turns": MAX_TURNS, "seconds": WALL_SECONDS},
        "endpoint_sha256": hashlib.sha256(settings.base_url.rstrip("/").encode()).hexdigest(),
        "credential_configured": bool(secret),
        "experiment_files": frozen_experiment,
        "operator_repairs": 0,
        "extra_attempts": 0,
        "python": sys.executable,
    }
    persist("launch.json", launch)
    result = {
        "source_commit": SOURCE_COMMIT,
        "operator_repairs": 0,
        "extra_attempts": 0,
        "stages_attempted": 0,
        "stage2": {"status": "not_started"},
    }
    records = []
    runtime = gate = None
    try:
        negative = await grade(support, root, 1)
        persist("stage1-empty-grade.json", negative)
        if negative.get("passed") or negative.get("tests", {}).get("executed") != 8:
            raise ValueError("invalid stage1 empty-directory negative control")
        task = TaskSpec(
            prompt=(HERE / "requirements.md").read_text(),
            max_steps=MAX_STEPS,
            max_turns=MAX_TURNS,
            require_observation=True,
            require_edit=True,
            require_verification=True,
            required_artifacts=["uniqwords.py", "README.md"],
        )
        result["stages_attempted"] = 1
        runtime, gate, first = await stage_one(root, settings, task, persist)
        result["stage1"] = first
        records = [first["before_fault"], first.get("after_resume")]
        first["independent_grade"] = await grade(support, root, 1)
        persist("stage1.json", first)
        if (
            first["status"] == "completed"
            and first["independent_grade"]["passed"]
            and first["budget_respected"]
        ):
            negative = await grade(support, root, 2)
            persist("stage2-negative-grade.json", negative)
            if negative.get("passed") or negative.get("tests") != {
                "expected": 12,
                "executed": 12,
                "passed": 8,
            }:
                raise ValueError("invalid stage2 pre-feature negative control")
            before_switch = runtime.settings.identity()
            prior_session, prior_run = runtime.state.session_id, runtime.state.run_id
            runtime.switch_model("compact-sol")
            selected = runtime.settings
            selection = {
                "before": before_switch,
                "after": selected.identity(),
                "same_model": selected.model == settings.model,
                "same_protocol": selected.protocol == settings.protocol,
                "same_endpoint": selected.base_url == settings.base_url,
                "same_credential": selected.api_key.get_secret_value() == secret,
                "same_session": runtime.state.session_id == prior_session,
                "other_settings_unchanged": {
                    key: value
                    for key, value in before_switch.items()
                    if key not in {"profile", "context_tokens"}
                }
                == {
                    key: value
                    for key, value in selected.identity().items()
                    if key not in {"profile", "context_tokens"}
                },
                "context_transition": settings.context_tokens == 128000
                and selected.context_tokens == 64000,
                "native_history_origins": runtime._history_origins,
            }
            persist("profile-switch.json", selection)
            if not all(
                selection[key]
                for key in (
                    "same_model",
                    "same_protocol",
                    "same_endpoint",
                    "same_credential",
                    "same_session",
                    "other_settings_unchanged",
                    "context_transition",
                )
            ):
                raise ValueError("compact profile changed unauthorized model coordinates")
            model = observed_model(selected)
            model.redaction_secrets = runtime.evidence.redactor.secrets
            runtime.model = model
            started = time.monotonic()
            runtime.continue_task(
                (HERE / "followup.md").read_text(), max_steps=MAX_STEPS, max_turns=MAX_TURNS
            )
            result["stages_attempted"] = 2
            second = await bounded_run(runtime, model, WALL_SECONDS - (time.monotonic() - started))
            records.append(second)
            result["stage2"] = second
            second["seconds"] = time.monotonic() - started
            second["budget_respected"] = (
                second["steps"] <= MAX_STEPS
                and second["turns"] <= MAX_TURNS
                and second["seconds"] <= WALL_SECONDS
                and second.get("error") != "TimeoutError"
            )
            second.update(
                continued_from=prior_run,
                profile_switch=selection,
                same_session=runtime.state.session_id == prior_session,
                independent_grade=await grade(support, root, 2),
            )
            persist("stage2.json", second)
    except (Exception, asyncio.CancelledError) as exc:
        result["driver_error"] = type(exc).__name__
        result["driver_message"] = redactor.text(str(exc))
    finally:
        result.update(totals(records, support))
        result["source_unchanged"] = shared.source_snapshot(SOURCE) == snapshot
        result["experiment_unchanged"] = experiment_snapshot() == frozen_experiment
        if gate is not None:
            result["successful_writes"] = gate.writes
            result["simulated_failures"] = int(gate.injection is not None)
        result["credentials"] = credential_scan(destination, secret)
        if runtime is not None:
            result["skills_used"] = bool(runtime.state.active_skills)
            result["memory_used"] = bool(runtime.state.recalled_ids)
            result["final_manifest"] = file_hashes(runtime)
        first, second = result.get("stage1", {}), result["stage2"]
        result["passed"] = bool(
            first.get("status") == "completed"
            and first.get("budget_respected")
            and first.get("independent_grade", {}).get("passed")
            and first.get("prior_success_ids_retained")
            and first.get("first_write_executed_once")
            and second.get("phase") == "completed"
            and second.get("budget_respected")
            and second.get("independent_grade", {}).get("passed")
            and result["source_unchanged"]
            and result["experiment_unchanged"]
            and not result["credentials"]["plaintext_found"]
            and not result.get("skills_used")
            and not result.get("memory_used")
            and not result.get("driver_error")
        )
        shared.write_json(destination / "result.json", redactor.value(result, mask_fields=False))
        print(
            json.dumps(
                {
                    key: result.get(key)
                    for key in (
                        "stages_attempted",
                        "real_model_requests",
                        "http_attempts",
                        "http_retries",
                        "source_unchanged",
                        "driver_error",
                    )
                }
            ),
            flush=True,
        )

    return 0 if result["passed"] else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-digest")
    parser.add_argument("--describe-source", action="store_true")
    parser.add_argument("--env-file", type=Path, default=HERE.parents[1] / ".env")
    args = parser.parse_args(argv)
    if args.describe_source:
        print(json.dumps(shared.source_snapshot(SOURCE), ensure_ascii=False, indent=2))
        return 0
    if not args.source_digest:
        parser.error("--source-digest is required")
    support, snapshot = shared.frozen_imports(SOURCE, args.source_digest)
    return asyncio.run(run_experiment(args, support, snapshot))


if __name__ == "__main__":
    raise SystemExit(main())
