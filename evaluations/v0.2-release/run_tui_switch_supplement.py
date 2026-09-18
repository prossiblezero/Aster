"""One bounded public-TUI supplement after the retained initial switch failure.

Uses only PTY keystrokes and read-only evidence; never changes first-run results.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from run_tui import (
    EXPLANATION,
    HERE,
    PYTHON,
    REPO,
    TERMINAL,
    Terminal,
    budget,
    digest,
    public_manifest,
    records,
)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--name", default="maintenance-switch-supplement")
    args = parser.parse_args(argv)
    if Path(sys.executable).resolve() != PYTHON:
        parser.error("Use the aster Python environment")
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip()
    if not sha.startswith(args.expected_commit):
        parser.error("HEAD differs from the explicitly approved repair commit")
    initial_path = HERE / "runs/maintenance-initial/summary.json"
    initial = json.loads(initial_path.read_text())
    assert not initial["passed"] and initial["failure"] == "model_switch_not_observed"
    workspace = Path(initial["workspace"])
    resume_id = "0de718b3eb334f8b91dea53355967dad"
    limits = {
        "steps": 50 - initial["consumed"]["steps"],
        "turns": 30 - initial["consumed"]["turns"],
        "seconds": min(467, int(900 - initial["elapsed_seconds"])),
    }
    assert limits == {"steps": 3, "turns": 9, "seconds": 467}
    output = HERE / "runs" / args.name
    if output.resolve().parent != (HERE / "runs").resolve():
        parser.error("name must be one directory component")
    output.mkdir(parents=True, exist_ok=False)
    excluded = {path.name for path in (workspace / ".aster/runs").glob("*")}
    initial_hash = digest(initial_path)
    before = public_manifest(workspace)
    command = [
        str(PYTHON.parent / "aster"),
        "--tui",
        "--cwd",
        str(workspace),
        "--env-file",
        str(REPO / ".env"),
        "--config",
        str(REPO / ".aster/v02-release.toml"),
        "--profile",
        "main",
        "--max-steps",
        str(limits["steps"]),
        "--max-turns",
        str(limits["turns"]),
    ]
    result = {
        "aster_sha": sha,
        "initial_result": str(initial_path.relative_to(REPO)),
        "initial_result_sha256": initial_hash,
        "original_failure_retained": True,
        "budget": limits,
        "command": command,
        "operations": [],
        "files_sha256": {
            path.name: digest(path)
            for path in [Path(__file__), HERE / "run_tui.py", REPO / ".aster/v02-release.toml"]
        },
        "initial_files": before,
    }
    (output / "manifest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    started = time.monotonic()
    terminal = Terminal(command, output / "terminal.ans")
    seen, active = set(), None
    stage, ready_at, explaining = "mount", started, False
    passed, failure, switched = False, "", False

    def operation(action, **fields):
        result["operations"].append(
            {"elapsed_seconds": round(time.monotonic() - started, 3), "action": action, **fields}
        )
        (output / "operations.json").write_text(
            json.dumps(result["operations"], ensure_ascii=False, indent=2) + "\n"
        )

    try:
        while True:
            states, events = records(workspace, excluded)
            used_steps, used_turns = budget(states, events)
            if time.monotonic() - started >= limits["seconds"]:
                failure = "wall_clock_budget"
                break
            if used_steps > limits["steps"] or used_turns > limits["turns"]:
                failure = "cumulative_budget_exceeded"
                break
            terminal.read()
            if terminal.process.poll() is not None:
                failure = "unexpected_tui_exit"
                break
            if stage == "mount":
                if b"Aster ready" in terminal.screen and time.monotonic() - ready_at >= 0.5:
                    terminal.text("/resume " + resume_id)
                    operation("submit", text="/resume " + resume_id)
                    stage, ready_at = "working", time.monotonic()
                elif time.monotonic() - ready_at > 20:
                    failure = "mount_timeout"
                    break
            for event in events:
                if event["hash"] in seen:
                    continue
                seen.add(event["hash"])
                if event["event"] == "run_continued":
                    active = event["run_id"]
                elif event["event"] == "permission_decision":
                    call = event["call"]
                    if event["decision"] == "ask":
                        if call["name"] not in {"run_tests", "run_shell"}:
                            failure = "unanticipated_approval:" + call["name"]
                            break
                        terminal.read(0.2)
                        terminal.key(b"\t\r")
                        operation("approve_once", call_id=call["call_id"], tool=call["name"])
                    elif call["name"] == "ask_user":
                        terminal.read(0.2)
                        answer = "实现已经完成，只确认已有成果和测试结果；本次补充验收不要求新增功能或修改文件。"
                        terminal.text(answer)
                        operation("answer", call_id=call["call_id"], text=answer)
                elif stage == "switching" and event["event"] == "model_changed":
                    if event["selected"].get("profile") == "continuation":
                        switched = True
                        operation(
                            "model_switch_observed", run_id=event["run_id"], profile="continuation"
                        )
                        stage, ready_at = "explain_ready", time.monotonic()
            if failure:
                break
            state = states.get(active)
            if stage == "working" and state and state["phase"] in TERMINAL:
                if state["phase"] != "completed":
                    failure = "run_stopped:" + state["stop_reason"]
                    break
                if explaining:
                    passed = switched
                    break
                stage, ready_at = "switch_ready", time.monotonic()
            if stage in {"switch_ready", "explain_ready"} and time.monotonic() - ready_at >= 0.5:
                if stage == "switch_ready":
                    if used_turns >= limits["turns"]:
                        failure = "no_remaining_model_round_for_explanation"
                        break
                    terminal.text("/model continuation")
                    operation("submit", text="/model continuation")
                    stage, ready_at = "switching", time.monotonic()
                else:
                    terminal.text(EXPLANATION)
                    operation("submit", text=EXPLANATION)
                    explaining, active, stage = True, None, "working"
            if stage == "switching" and time.monotonic() - ready_at > 15:
                failure = "model_switch_not_observed"
                break
    except BaseException as exc:
        failure = f"controller_error:{type(exc).__name__}:{exc}"
        raise
    finally:
        terminal.close()
        states, events = records(workspace, excluded)
        used_steps, used_turns = budget(states, events)
        after = public_manifest(workspace)
        if before != after:
            passed, failure = False, "product_changed_during_lifecycle_supplement"
        if used_steps > limits["steps"] or used_turns > limits["turns"]:
            passed, failure = False, "cumulative_budget_exceeded"
        assert digest(initial_path) == initial_hash, "Initial outcome must never be overwritten"
        result.update(
            passed=passed,
            failure=failure,
            model_switched=switched,
            elapsed_seconds=round(time.monotonic() - started, 3),
            exit_code=terminal.process.returncode,
            consumed={"steps": used_steps, "turns": used_turns},
            cumulative={
                "steps": initial["consumed"]["steps"] + used_steps,
                "turns": initial["consumed"]["turns"] + used_turns,
                "seconds": round(initial["elapsed_seconds"] + time.monotonic() - started, 3),
            },
            runs=[
                {
                    key: state[key]
                    for key in (
                        "run_id",
                        "session_id",
                        "phase",
                        "stop_reason",
                        "step",
                        "turn",
                        "usage",
                        "changed_paths",
                        "verification",
                        "feedback",
                        "final",
                    )
                }
                for state in states.values()
            ],
            final_files=after,
            product_bytes_unchanged=before == after,
        )
        (output / "events.json").write_text(json.dumps(events, ensure_ascii=False, indent=2) + "\n")
        (output / "summary.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        )
        print(
            json.dumps(
                {
                    key: result[key]
                    for key in (
                        "passed",
                        "failure",
                        "model_switched",
                        "consumed",
                        "cumulative",
                        "product_bytes_unchanged",
                    )
                },
                ensure_ascii=False,
            )
        )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
