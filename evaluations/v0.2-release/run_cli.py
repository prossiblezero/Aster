"""Drive the public CLI, recording operator answers and a cumulative bounded run.

No Runtime/model monkeypatches or candidate implementation edits. Read-only trace
monitoring supplies predeclared clarification and already-authorized approvals.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import signal
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("name")
    parser.add_argument("workspace", type=Path)
    parser.add_argument("prompt", type=Path)
    parser.add_argument("--answer", type=Path)
    parser.add_argument("--steps", type=int, required=True)
    parser.add_argument("--turns", type=int, required=True)
    parser.add_argument("--seconds", type=float, required=True)
    parser.add_argument("--resume")
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    result = Path(__file__).parent / "runs" / args.name
    result.mkdir(parents=True, exist_ok=False)
    initial_runs = {p.name for p in (workspace / ".aster/runs").glob("*")}
    command = [
        str(Path(sys.executable).parent / "aster"), "--cwd", str(workspace),
        "--env-file", str(REPO / ".env"), "--config", str(REPO / ".aster/v02-release.toml"),
        "--profile", "main", "--max-steps", str(args.steps), "--max-turns", str(args.turns),
    ]
    if args.resume:
        command += ["--resume", args.resume]
    command.append(args.prompt.read_text())
    aster_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip()
    source_tree = subprocess.check_output(["git", "rev-parse", "HEAD:src"], cwd=REPO, text=True).strip()
    (result / "launch.json").write_text(json.dumps({
        "aster_sha": aster_sha, "source_tree": source_tree,
        "prompt_sha256": hashlib.sha256(args.prompt.read_bytes()).hexdigest(),
    }, indent=2) + "\n")
    started = time.monotonic()
    answered, seen, questions, timed_out = [], set(), 0, False
    with (result / "stdout.txt").open("w") as out, (result / "stderr.txt").open("w") as err:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=out, stderr=err, text=True)
        try:
            while process.poll() is None:
                if time.monotonic() - started >= args.seconds:
                    timed_out = True
                    process.send_signal(signal.SIGINT)
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                    break
                for path in (workspace / ".aster/runs").glob("*/trace.jsonl"):
                    if path.parent.name in initial_runs:
                        continue
                    for line in path.read_text().splitlines():
                        try:
                            event = json.loads(line)
                        except ValueError:
                            continue  # A line currently being appended is not an event yet.
                        if event["hash"] in seen:
                            continue
                        seen.add(event["hash"])
                        if event["event"] != "permission_decision":
                            continue
                        call = event["call"]
                        response = None
                        if event["decision"] == "ask":
                            response = "y"
                        elif call["name"] == "ask_user":
                            questions += 1
                            response = (
                                args.answer.read_text().strip().replace("\n", " ")
                                if args.answer and questions <= 2
                                else "按已经提供的需求和合理默认实现，不扩展范围。"
                            )
                        if response is not None:
                            answered.append({"run_id": event["run_id"], "call_id": call["call_id"],
                                             "tool": call["name"], "response": response})
                            process.stdin.write(response + "\n")
                            process.stdin.flush()
                time.sleep(0.1)
        except BaseException:
            if process.poll() is None:
                process.send_signal(signal.SIGINT)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            raise
        finally:
            process.stdin.close()
    runs = []
    for path in (workspace / ".aster/runs").glob("*/checkpoint.json"):
        if path.parent.name in initial_runs:
            continue
        data = json.loads(path.read_text())
        state = data["state"]
        runs.append({key: state[key] for key in (
            "run_id", "session_id", "phase", "stop_reason", "step", "turn", "usage",
            "changed_paths", "verification", "feedback",
        )})
        # Complete raw traces/checkpoints stay locally under .aster; retain the
        # operational events needed to audit costs and user-visible actions here.
        events = [json.loads(line) for line in (path.parent / "trace.jsonl").read_text().splitlines()]
        keep = {"provider_attempt", "provider_error", "permission_decision", "tool_result",
                "run_started", "run_stopped", "run_completed", "model_changed"}
        (result / f"{state['run_id']}-events.json").write_text(json.dumps(
            [event for event in events if event["event"] in keep], ensure_ascii=False, indent=2
        ) + "\n")
    summary = {
        "aster_sha": aster_sha, "source_tree": source_tree,
        "python": sys.executable, "exit_code": process.returncode,
        "elapsed_seconds": round(time.monotonic() - started, 3), "timed_out": timed_out,
        "budget": {"steps": args.steps, "turns": args.turns, "seconds": args.seconds},
        "prompt_sha256": hashlib.sha256(args.prompt.read_bytes()).hexdigest(),
        "settings_sha256": hashlib.sha256((Path(__file__).parent / "settings.toml").read_bytes()).hexdigest(),
        "clarifications": questions, "operator_inputs": answered, "runs": runs,
    }
    (result / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: summary[k] for k in ("exit_code", "elapsed_seconds", "timed_out", "clarifications", "runs")}, ensure_ascii=False))
    return 0 if process.returncode == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
