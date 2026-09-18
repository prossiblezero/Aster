"""Drive the real Aster TUI with PTY keys and read-only trace observation.

Frozen maintenance lifecycle only: edit -> cancel generation -> quit -> resume
with remaining budgets -> idle model switch -> explanation. No model/Runtime API.
"""

from __future__ import annotations

import argparse
import errno
import fcntl
import hashlib
import json
import os
import pty
import select
import signal
import struct
import subprocess
import sys
import termios
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
PYTHON = Path("/home/dzh/.conda/envs/aster/bin/python").resolve()
EXPLANATION = "请基于刚才的会话，用中文简要说明撤销删除如何工作、哪些自测已经执行、有什么验证边界；不要修改文件或重复执行工具。"
TERMINAL = {"completed", "stopped", "failed"}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def public_manifest(workspace):
    return {
        str(path.relative_to(workspace)): digest(path)
        for path in sorted(workspace.rglob("*"))
        if path.is_file()
        and not path.is_symlink()
        and not any(
            part.startswith(".") or part in {"node_modules", "__pycache__"}
            for part in path.relative_to(workspace).parts
        )
    }


def read_json(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


class Terminal:
    def __init__(self, command, recording):
        self.master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 48, 160, 0, 0))
        self.stream = recording.open("wb")
        self.process = subprocess.Popen(
            command,
            stdin=slave,
            stdout=slave,
            stderr=slave,
            cwd=REPO,
            env={**os.environ, "TERM": "xterm-256color"},
            start_new_session=True,
        )
        os.close(slave)
        self.screen = b""

    def read(self, timeout=0.1):
        ready, _, _ = select.select([self.master], [], [], timeout)
        if not ready:
            return
        try:
            data = os.read(self.master, 65536)
        except OSError as exc:
            if exc.errno != errno.EIO:
                raise
            return
        self.stream.write(data)
        self.stream.flush()
        self.screen = (self.screen + data)[-1024 * 1024 :]

    def key(self, value):
        os.write(self.master, value)

    def text(self, value):
        value = value.replace("\r", " ").replace("\n", " ")
        self.key(b"\x1b[200~" + value.encode() + b"\x1b[201~\r")

    def close(self):
        if self.process.poll() is None:
            self.key(b"\x03")
            deadline = time.monotonic() + 2
            while self.process.poll() is None and time.monotonic() < deadline:
                self.read()
            if self.process.poll() is None:
                self.key(b"\x11")
            deadline = time.monotonic() + 5
            while self.process.poll() is None and time.monotonic() < deadline:
                self.read()
            if self.process.poll() is None:
                os.killpg(self.process.pid, signal.SIGTERM)
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    os.killpg(self.process.pid, signal.SIGKILL)
                    self.process.wait(timeout=2)
        self.read(0)
        self.stream.close()
        os.close(self.master)


def records(workspace, excluded):
    states, events = {}, []
    for directory in sorted((workspace / ".aster/runs").glob("*")):
        if directory.name in excluded:
            continue
        checkpoint = read_json(directory / "checkpoint.json")
        if checkpoint:
            states[directory.name] = checkpoint["state"]
        trace = directory / "trace.jsonl"
        if trace.exists():
            for line in trace.read_text().splitlines():
                try:
                    events.append(json.loads(line))
                except ValueError:
                    pass  # The writer may still be appending the final line.
    events.sort(
        key=lambda event: (event.get("time", ""), event.get("run_id", ""), event.get("seq", 0))
    )
    return states, events


def budget(states, events):
    steps = {key: value["step"] for key, value in states.items()}
    turns = {key: value["turn"] for key, value in states.items()}
    began = {}
    for event in events:
        run = event["run_id"]
        if event["event"] == "tool_result":
            steps[run] = max(steps.get(run, 0), event.get("step", 0))
        if (
            event["event"] == "model_stream"
            and event.get("type") == "begin"
            and event.get("attempt") == 1
        ):
            began[run] = began.get(run, 0) + 1
    for run, count in began.items():
        turns[run] = max(turns.get(run, 0), count)
    return sum(steps.values()), sum(turns.values())


def generation_pending(begin, events):
    """Do not cancel on a stale begin after the response has already arrived."""
    return not any(
        event["run_id"] == begin["run_id"]
        and event["seq"] > begin["seq"]
        and (
            event["event"] in {"provider_attempt", "model_reply", "provider_error"}
            or (event["event"] == "model_stream" and event.get("type") in {"completed", "error"})
        )
        for event in events
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("name")
    parser.add_argument("workspace", type=Path)
    parser.add_argument("--prompt", type=Path, default=HERE / "maintenance-prompt.md")
    parser.add_argument("--steps", type=int, default=50)
    parser.add_argument("--turns", type=int, default=30)
    parser.add_argument("--seconds", type=float, default=900)
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="Mount, switch model and quit TUI; sends no task and makes no model request",
    )
    args = parser.parse_args(argv)
    if Path(sys.executable).resolve() != PYTHON:
        parser.error("Run with the aster environment Python")
    workspace = args.workspace.resolve()
    if not workspace.is_dir() or args.steps < 1 or args.turns < 1 or args.seconds <= 0:
        parser.error("existing candidate and positive budgets required")
    output = HERE / "runs" / args.name
    if output.resolve().parent != (HERE / "runs").resolve():
        parser.error("name must be a single directory name")
    output.mkdir(parents=True, exist_ok=False)
    excluded = {path.name for path in (workspace / ".aster/runs").glob("*")}
    started = time.monotonic()
    summary = {
        "aster_sha": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO, text=True
        ).strip(),
        "python": sys.executable,
        "workspace": str(workspace),
        "smoke": args.smoke,
        "budget": {"steps": args.steps, "turns": args.turns, "seconds": args.seconds},
        "files_sha256": {
            path.name: digest(path)
            for path in [
                Path(__file__),
                args.prompt,
                HERE / "maintenance-protocol.md",
                HERE / "maintenance-grader.py",
                HERE.parent / "benefit-20260918/check_browser.py",
                REPO / ".aster/v02-release.toml",
            ]
        },
        "initial_files": public_manifest(workspace),
        "operations": [],
        "processes": [],
    }
    (output / "manifest.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    terminal, active, resume, profile = None, None, None, "main"
    stage, mode, ready_at = "launch", "work", 0.0
    seen, had_edit, cancel_done, switched, recoveries = set(), False, False, False, 0
    cancel_target, failure, passed = None, "", False
    states, events = {}, []

    def operation(action, **fields):
        summary["operations"].append(
            {"elapsed_seconds": round(time.monotonic() - started, 3), "action": action, **fields}
        )
        (output / "operations.json").write_text(
            json.dumps(summary["operations"], ensure_ascii=False, indent=2) + "\n"
        )

    try:
        while True:
            elapsed = time.monotonic() - started
            states, events = records(workspace, excluded)
            used_steps, used_turns = budget(states, events)
            if elapsed >= args.seconds:
                failure = "wall_clock_budget"
                break
            if used_steps > args.steps or used_turns > args.turns:
                failure = "cumulative_budget_exceeded"
                break
            if stage == "launch":
                remaining_steps, remaining_turns = args.steps - used_steps, args.turns - used_turns
                if min(remaining_steps, remaining_turns) <= 0:
                    failure = "no_remaining_budget"
                    break
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
                    profile,
                    "--max-steps",
                    str(remaining_steps),
                    "--max-turns",
                    str(remaining_turns),
                ]
                number = len(summary["processes"]) + 1
                terminal = Terminal(command, output / f"terminal-{number}.ans")
                summary["processes"].append({"number": number, "command": command})
                ready_at = time.monotonic()
                stage = "mount"
                operation(
                    "launch",
                    process=number,
                    remaining_steps=remaining_steps,
                    remaining_turns=remaining_turns,
                )
            terminal.read()
            if terminal.process.poll() is not None:
                if stage != "quit":
                    failure = "unexpected_tui_exit"
                    break
                summary["processes"][-1]["exit_code"] = terminal.process.returncode
                terminal.close()
                terminal = None
                if args.smoke:
                    passed = True
                    break
                stage = "launch"
                continue
            if stage == "mount":
                if b"Aster ready" in terminal.screen and time.monotonic() - ready_at >= 0.5:
                    if args.smoke:
                        terminal.text("/model continuation")
                        operation("submit_smoke", text="/model continuation")
                        stage, ready_at = "smoke_switch", time.monotonic()
                    else:
                        text = "/resume " + resume if resume else args.prompt.read_text()
                        terminal.text(text)
                        operation("submit", text=text)
                        active = None
                        stage = "working"
                elif time.monotonic() - ready_at > 20:
                    failure = "tui_mount_timeout"
                    break
            if stage == "smoke_switch":
                if b"Selected continuation for this conversation." in terminal.screen:
                    terminal.key(b"\x11")
                    operation("ctrl_q_smoke_after_switch")
                    stage, ready_at = "quit", time.monotonic()
                elif time.monotonic() - ready_at > 10:
                    failure = "smoke_model_switch_not_rendered"
                    break
            for event in events:
                if event["hash"] in seen:
                    continue
                seen.add(event["hash"])
                kind = event["event"]
                if kind in {"run_started", "run_continued"}:
                    active = event["run_id"]
                if kind == "permission_decision":
                    call = event["call"]
                    if event["decision"] == "ask":
                        # The frozen maintenance task authorizes local edits/tests.
                        # Keyboard focus starts at Deny; Tab selects Allow once.
                        if call["name"] not in {
                            "write_file",
                            "patch_file",
                            "run_shell",
                            "run_tests",
                        }:
                            failure = "unanticipated_approval:" + call["name"]
                            break
                        terminal.read(0.2)
                        terminal.key(b"\t\r")
                        operation(
                            "approve_once",
                            run_id=event["run_id"],
                            call_id=call["call_id"],
                            tool=call["name"],
                        )
                    elif call["name"] == "ask_user":
                        terminal.read(0.2)
                        answer = "请按照已提供的撤销删除需求和项目原有约定实现，不增加范围；你可以自行选择不影响验收的界面细节。"
                        terminal.text(answer)
                        operation(
                            "answer", run_id=event["run_id"], call_id=call["call_id"], text=answer
                        )
                if kind == "tool_result" and event["outcome"].get("changed_paths"):
                    had_edit = True
                if (
                    stage == "working"
                    and mode == "work"
                    and not cancel_done
                    and had_edit
                    and kind == "model_stream"
                    and event.get("type") == "begin"
                    and generation_pending(event, events)
                ):
                    terminal.key(b"\x03")
                    cancel_target = event["run_id"]
                    stage = "cancelling"
                    operation(
                        "ctrl_c_after_edit", run_id=cancel_target, attempt=event.get("attempt")
                    )
                if (
                    stage == "switching"
                    and kind == "model_changed"
                    and event["selected"].get("profile") == "continuation"
                ):
                    switched, profile = True, "continuation"
                    mode, stage, ready_at = "explain", "explain_ready", time.monotonic()
                    operation("model_switch_observed", run_id=event["run_id"], profile=profile)
            if failure:
                break
            state = states.get(active)
            if stage == "cancelling":
                cancelled = states.get(cancel_target)
                if cancelled and cancelled["phase"] in TERMINAL:
                    if cancelled["stop_reason"] not in {"cancelled", "user_abort"}:
                        failure = "intentional_cancel_missed_generation"
                        break
                    if (workspace / ".aster/runs" / cancel_target / "inflight.json").exists():
                        failure = "unsettled_tool_on_cancel"
                        break
                    cancel_done, resume = True, cancel_target
                    stage, ready_at = "quit_ready", time.monotonic()
                    operation(
                        "cancel_checkpoint_settled",
                        run_id=resume,
                        step=cancelled["step"],
                        turn=cancelled["turn"],
                    )
            elif stage == "working" and state and state["phase"] in TERMINAL:
                if state["phase"] == "completed":
                    if mode == "explain":
                        passed = cancel_done and switched
                        break
                    if not cancel_done:
                        failure = "completed_without_cancel_trigger"
                        break
                    stage, ready_at = "switch_ready", time.monotonic()
                elif state["stop_reason"] == "provider_error" and recoveries < 1:
                    recoveries += 1
                    resume = state["run_id"]
                    stage, ready_at = "quit_ready", time.monotonic()
                    operation("service_failure_recovery", run_id=resume, count=recoveries)
                else:
                    failure = "run_stopped:" + state["stop_reason"]
                    break
            if (
                stage in {"quit_ready", "switch_ready", "explain_ready"}
                and time.monotonic() - ready_at >= 0.5
            ):
                if stage == "quit_ready":
                    terminal.key(b"\x11")
                    operation("ctrl_q")
                    stage = "quit"
                elif stage == "switch_ready":
                    if args.turns - used_turns <= 0:
                        failure = "no_budget_for_model_switch_verification"
                        break
                    terminal.text("/model continuation")
                    operation("submit", text="/model continuation")
                    stage, ready_at = "switching", time.monotonic()
                else:
                    terminal.text(EXPLANATION)
                    operation("submit", text=EXPLANATION)
                    active, stage = None, "working"
            if stage == "switching" and time.monotonic() - ready_at > 15:
                failure = "model_switch_not_observed"
                break
            if stage == "quit" and time.monotonic() - ready_at > 20:
                failure = "tui_quit_timeout"
                break
    except BaseException as exc:
        failure = f"controller_error:{type(exc).__name__}:{exc}"
        raise
    finally:
        if terminal is not None:
            terminal.close()
            summary["processes"][-1]["exit_code"] = terminal.process.returncode
        states, events = records(workspace, excluded)
        used_steps, used_turns = budget(states, events)
        decided = [event["call_id"] for event in events if event["event"] == "tool_decision"]
        duplicate_ids = sorted({call_id for call_id in decided if decided.count(call_id) > 1})
        if duplicate_ids:
            passed, failure = False, "replayed_tool_call_ids"
        if used_steps > args.steps or used_turns > args.turns:
            passed, failure = False, "cumulative_budget_exceeded"
        generation_cancelled = any(
            event["run_id"] == cancel_target
            and event["event"] == "provider_attempt"
            and event.get("classification") == "cancelled"
            for event in events
        )
        if cancel_done and not generation_cancelled:
            passed, failure = False, "cancellation_not_confirmed_during_generation"
        summary.update(
            passed=passed,
            generation_cancelled=generation_cancelled,
            failure=failure,
            intentional_cancel=cancel_done,
            model_switched=switched,
            service_recoveries=recoveries,
            elapsed_seconds=round(time.monotonic() - started, 3),
            consumed={"steps": used_steps, "turns": used_turns},
            duplicate_call_ids=duplicate_ids,
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
                    )
                }
                for state in states.values()
            ],
            final_files=public_manifest(workspace),
        )
        (output / "events.json").write_text(json.dumps(events, ensure_ascii=False, indent=2) + "\n")
        (output / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
        )
        print(
            json.dumps(
                {
                    key: summary[key]
                    for key in (
                        "passed",
                        "failure",
                        "consumed",
                        "elapsed_seconds",
                        "intentional_cancel",
                        "model_switched",
                    )
                },
                ensure_ascii=False,
            )
        )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
