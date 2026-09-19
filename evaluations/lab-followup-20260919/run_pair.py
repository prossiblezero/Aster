"""Run one frozen Aster CLI arm, preserving evidence outside its empty workspace.

Use the aster environment's Python. --probe-only checks boundaries in a disposable
workspace without credentials or model calls. Each arm needs a distinct --name.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import subprocess
import tempfile
import threading
import time
import tomllib
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
RESULTS = Path(__file__).resolve().parent / "runs"
PYTHON = Path("/home/dzh/.conda/envs/aster/bin/python")
ANSWER = "按已经提供的需求和合理默认实现，不扩展范围。"
MAX_CLARIFICATIONS = 2
PROXY_VARIABLES = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
                   "http_proxy", "https_proxy", "all_proxy", "no_proxy")
STATE_FIELDS = ("run_id", "session_id", "phase", "stop_reason", "step", "turn", "usage",
                "changed_paths", "verification", "feedback", "final")
EVENTS = {"provider_attempt", "provider_error", "permission_decision", "tool_result",
          "run_started", "run_stopped", "run_completed", "model_changed", "run_continued",
          "run_resumed", "verifier_result", "completion_revision", "stall_detected"}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def manifest(source: Path) -> dict:
    files = {str(path.relative_to(source)): sha(path.read_bytes())
             for path in sorted((source / "src").rglob("*.py"))}
    if not files or "src/aster/cli.py" not in files:
        raise ValueError("--source must contain the frozen src/aster/cli.py")
    if (source / "pyproject.toml").is_file():
        files["pyproject.toml"] = sha((source / "pyproject.toml").read_bytes())
    declared = json.loads((source / "SOURCE.json").read_text()) if (source / "SOURCE.json").exists() else {}
    if declared and declared.get("files") != files:
        raise ValueError("frozen source differs from its SOURCE.json manifest")
    return {"source": str(source), "files": files, "commit": declared.get("commit"),
            "sha256": sha(json.dumps(files, sort_keys=True).encode())}


def child_environment(source: Path) -> dict:
    # Credentials/configuration come only from the explicitly selected files.
    env = {key: os.environ[key] for key in
           ("HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "TERM", *PROXY_VARIABLES)
           if key in os.environ}
    env.update(PATH=f"{PYTHON.parent}:/usr/local/bin:/usr/bin:/bin",
               PYTHONPATH=str(source / "src"), PYTHONNOUSERSITE="1",
               PYTHONSAFEPATH="1", PYTHONDONTWRITEBYTECODE="1", PYTHONUNBUFFERED="1")
    return env


class Redactor:
    """Keep explicit credential values and endpoint hosts out of exported evidence."""

    def __init__(self, env_file: Path | None = None, config: Path | None = None):
        values = [os.environ[name] for name in PROXY_VARIABLES if os.environ.get(name)]
        env_values = {}
        if env_file:
            for line in env_file.read_text().splitlines():
                if "=" in line and not line.lstrip().startswith("#"):
                    key, value = line.split("=", 1)
                    env_values[key.strip()] = value.strip().strip("\"'")
                    if re.search(r"(?i)(key|token|secret|password|url|endpoint)", key):
                        values.append(value.strip().strip("\"'"))

        def collect(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    if isinstance(item, str) and key in {"base_url", "api_key", "password", "secret"}:
                        values.append(item)
                    if key == "api_key_env" and isinstance(item, str) and item in env_values:
                        values.append(env_values[item])
                    collect(item)
            elif isinstance(value, list):
                for item in value:
                    collect(item)

        if config:
            collect(tomllib.loads(config.read_text()))
        for value in values[:]:
            if value.startswith(("http://", "https://")):
                values.extend([value.rstrip("/"), urlsplit(value).netloc])
        self.values = sorted({value for value in values if value}, key=len, reverse=True)

    def text(self, value: str) -> str:
        for secret in self.values:
            value = value.replace(secret, "[REDACTED]")
        return re.sub(r"(?i)\b(?:sk-[a-z0-9_-]{12,}|bearer\s+[a-z0-9._-]{12,})",
                      "[REDACTED]", value)

    def value(self, value):
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, list):
            return [self.value(item) for item in value]
        if isinstance(value, dict):
            return {self.text(key): "[REDACTED]" if re.fullmatch(
                r"(?i)(api_key|password|secret|authorization|base_url)", key
            ) else self.value(item) for key, item in value.items()}
        return value


def write_json(path: Path, data, redactor: Redactor):
    path.write_text(json.dumps(redactor.value(data), ensure_ascii=False, indent=2) + "\n")


IMPORT_PROBE = r'''
import hashlib, importlib, json, pathlib, sys
source = pathlib.Path(sys.argv[1]).resolve()
modules = {}
for name in ("aster", "aster.cli", "aster.runtime", "aster.context", "aster.safety.runner"):
    path = pathlib.Path(importlib.import_module(name).__file__).resolve()
    if not path.is_relative_to(source / "src"):
        raise RuntimeError("frozen source import boundary violated")
    modules[name] = {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
result = {"python": sys.executable, "prefix": sys.prefix, "modules": modules}
if len(sys.argv) > 2:
    from aster.config import load_settings
    settings = load_settings(pathlib.Path(sys.argv[2]), pathlib.Path(sys.argv[3]), profile=sys.argv[4])
    if settings.sandbox != "required":
        raise RuntimeError("evaluation requires sandbox=required")
    result["settings"] = settings.identity()
print(json.dumps(result))
'''


ISOLATION_PROBE = r"""
import asyncio, json, pathlib, shlex, tempfile, sys
from aster.config import Settings
from aster.safety.runner import SandboxRunner
from aster.workspace import Workspace, BoundaryError
targets = json.loads(sys.argv[1])
async def check():
    with tempfile.TemporaryDirectory(prefix="aster-lab-probe-") as directory:
        root = pathlib.Path(directory)
        (root / "local.txt").write_text("local-probe")
        workspace = Workspace(root)
        direct = []
        for target in targets:
            try:
                workspace.path(target["path"])
            except BoundaryError as exc:
                direct.append({**target, "denied": True, "reason": str(exc)})
            else:
                direct.append({**target, "denied": False})
        program = '''
import json, pathlib
assert pathlib.Path("local.txt").read_text() == "local-probe"
checks = []
for target in TARGETS:
    path = pathlib.Path(target["path"])
    try:
        if target["directory"]:
            next(path.iterdir(), None)
        else:
            with path.open("rb") as stream:
                stream.read(1)
    except PermissionError:
        checks.append({**target, "denied": True})
    except OSError as exc:
        checks.append({**target, "denied": False, "error": type(exc).__name__})
    else:
        checks.append({**target, "denied": False})
print(json.dumps(checks))
assert all(item["denied"] for item in checks), "outside read was not permission denied"
'''
        program = "TARGETS = " + repr(targets) + "\n" + program
        runner = SandboxRunner(Settings(sandbox="required", network=True, reuse_environment=True))
        try:
            outcome = await runner.run(workspace, "python -c " + shlex.quote(program), 20, readonly=True)
        finally:
            runner.close()
        result = {"workspace": direct, "shell": outcome.model_dump(mode="json")}
        result["passed"] = all(item["denied"] for item in direct) and outcome.status == "success" and outcome.data.get("isolation") == "landlock+seccomp"
        print(json.dumps(result))
        return 0  # Persist failed boundary checks too; the outer command exits 1.
raise SystemExit(asyncio.run(check()))
"""


def capture_json(program: str, arguments: list[str], cwd: Path, env: dict, redactor: Redactor):
    completed = subprocess.run([str(PYTHON), "-c", program, *arguments], cwd=cwd,
                               env=env, capture_output=True, text=True, timeout=60)
    if completed.returncode:
        raise RuntimeError(redactor.text(completed.stderr or completed.stdout or "probe failed"))
    return json.loads(completed.stdout)


def stop(process):
    if process.poll() is None:
        process.send_signal(signal.SIGINT)
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def events(path: Path):
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        try:
            yield json.loads(line)
        except ValueError:
            continue  # An incomplete final line may still be in flight.


def responses(event: dict, questions: int):
    if event.get("event") != "permission_decision" or event.get("decision") == "deny":
        return [], questions, False
    result = [("approval", "y")] if event.get("decision") == "ask" else []
    if event.get("call", {}).get("name") == "ask_user":
        questions += 1
        if questions > MAX_CLARIFICATIONS:
            return [], questions, True
        result.append(("clarification", ANSWER))
    return result, questions, False


def collect_runs(workspace: Path, result: Path, redactor: Redactor, initial_runs=frozenset()) -> list[dict]:
    summaries = []
    for directory in sorted((workspace / ".aster/runs").glob("*")):
        if not directory.is_dir() or directory.name in initial_runs:
            continue
        trace = directory / "trace.jsonl"
        rows = list(events(trace))
        with (result / f"{directory.name}-events.jsonl").open("w") as output:
            for event in rows:
                if event.get("event") in EVENTS:
                    output.write(json.dumps(redactor.value(event), ensure_ascii=False) + "\n")
        checkpoint = directory / "checkpoint.json"
        summary = {"run_id": directory.name, "checkpoint_present": checkpoint.exists(),
                   "trace_sha256": sha(trace.read_bytes()) if trace.exists() else None,
                   "usage_note": "Reported usage only; missing attempt usage is unknown, not zero."}
        if checkpoint.exists():
            data = json.loads(checkpoint.read_text())
            state = data["state"]
            state = {key: state.get(key) for key in STATE_FIELDS}
            write_json(result / f"{directory.name}-state.json", state, redactor)
            summary.update(state)
            summary["checkpoint_sha256"] = sha(checkpoint.read_bytes())
        summaries.append(summary)
    return summaries


def drive(command, workspace, env, result, seconds, redactor, initial_runs=frozenset()):
    started = time.monotonic()
    process = subprocess.Popen(command, cwd=workspace, env=env, stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                               encoding="utf-8", errors="replace")
    reader_errors = []

    def copy(stream, path):
        try:
            with path.open("w") as output:
                for line in stream:
                    output.write(redactor.text(line))
                    output.flush()
        except Exception as exc:
            reader_errors.append(type(exc).__name__)
        finally:
            stream.close()

    threads = [threading.Thread(target=copy, args=(stream, result / name), daemon=True)
               for stream, name in ((process.stdout, "stdout.txt"), (process.stderr, "stderr.txt"))]
    for thread in threads:
        thread.start()
    inputs, seen, questions, reason = [], set(), 0, None
    try:
        while process.poll() is None:
            if time.monotonic() - started >= seconds:
                reason = "wall_time_limit"
                break
            for trace in (workspace / ".aster/runs").glob("*/trace.jsonl"):
                if trace.parent.name in initial_runs:
                    continue
                for event in events(trace):
                    identity = (event.get("run_id"), event.get("hash", event.get("seq")))
                    if identity in seen:
                        continue
                    seen.add(identity)
                    answers, questions, limit = responses(event, questions)
                    if limit:
                        reason = "clarification_limit"
                        break
                    for kind, answer in answers:
                        process.stdin.write(answer + "\n")
                        process.stdin.flush()
                        inputs.append({"run_id": event.get("run_id"),
                                       "call_id": event["call"]["call_id"],
                                       "tool": event["call"]["name"], "kind": kind,
                                       "response": answer, "elapsed_seconds": round(time.monotonic() - started, 3)})
                if reason:
                    break
            if reason:
                break
            time.sleep(0.1)
    except KeyboardInterrupt:
        reason = "operator_cancelled"
    except Exception as exc:
        reason = "driver_error:" + type(exc).__name__
    finally:
        stop(process)
        process.stdin.close()
        for thread in threads:
            thread.join(timeout=20)
        if any(thread.is_alive() for thread in threads):
            reader_errors.append("output_reader_incomplete")
    return {"exit_code": process.returncode, "elapsed_seconds": round(time.monotonic() - started, 3),
            "driver_stop_reason": reason, "timed_out": reason == "wall_time_limit",
            "clarifications": questions, "operator_inputs": inputs, "reader_errors": reader_errors}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--source-commit", help="Optional provenance label; source hashes are authoritative")
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--name", required=True)
    parser.add_argument("--prompt", type=Path)
    parser.add_argument("--resume", help="Prior run ID; pass the remaining budgets explicitly")
    parser.add_argument("--allow-source-change", action="store_true",
                        help="Explicit diagnostic revision only: resume with a newly frozen source")
    parser.add_argument("--profile-config", type=Path, default=ROOT / ".aster/v02-release.toml")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--profile", default="main")
    parser.add_argument("--steps", type=int, default=80)
    parser.add_argument("--turns", type=int, default=45)
    parser.add_argument("--seconds", type=float, default=1500)
    parser.add_argument("--probe-only", action="store_true")
    parser.add_argument("--forbid-path", type=Path, action="append", default=[])
    args = parser.parse_args()
    if args.allow_source_change and not args.resume:
        parser.error("--allow-source-change requires --resume")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", args.name):
        parser.error("--name must be a simple result-directory name")
    if not 1 <= args.steps <= 500 or not 1 <= args.turns <= 500 or args.seconds <= 0:
        parser.error("budgets must be positive; steps and turns must be <= 500")
    source = args.source.resolve(strict=True)
    before = manifest(source)
    env = child_environment(source)
    redactor = Redactor() if args.probe_only else Redactor(args.env_file, args.profile_config)
    result = RESULTS / args.name
    if not args.probe_only:
        if args.workspace is None or (args.prompt is None and not args.resume):
            parser.error("--workspace and --prompt are required for a new real run")
        workspace = args.workspace.resolve()
        if source.is_relative_to(workspace) or result.is_relative_to(workspace):
            parser.error("source and results must remain outside the product workspace")
        if not args.resume and workspace.exists() and (not workspace.is_dir() or any(workspace.iterdir())):
            parser.error("workspace must be a new empty directory")
        if args.resume:
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", args.resume):
                parser.error("invalid --resume run ID")
            prior = next((json.loads(path.read_text()) for path in RESULTS.glob("*/summary.json")
                          if any(run.get("run_id") == args.resume
                                 for run in json.loads(path.read_text()).get("runs", []))), None)
            if prior is None or any(prior.get(key) != value for key, value in {
                "workspace": str(workspace),
                "profile_config_sha256": sha(args.profile_config.read_bytes()), "profile": args.profile,
            }.items()):
                parser.error("resume requires a recorded run with the same workspace and profile config")
            if prior["source_sha256"] != before["sha256"] and not args.allow_source_change:
                parser.error("resume source changed; use --allow-source-change only for a recorded diagnostic revision")
        prompt = args.prompt.read_bytes().decode("utf-8") if args.prompt else ""
        workspace.mkdir(parents=True, exist_ok=True)
        initial_runs = {path.name for path in (workspace / ".aster/runs").glob("*")}
    result.mkdir(parents=True, exist_ok=False)
    write_json(result / "source-manifest.json", {**before, "commit_label": args.source_commit}, redactor)
    if args.probe_only:
        targets = args.forbid_path or [
            ROOT / "src/aster/runtime.py",
            ROOT / "evaluations/v0.2-release/candidates/lab_data/lab_data.py",
            ROOT / "evaluations/v0.2-release/lab-known-issues.md",
            Path(__file__).with_name("grader.py"),
        ]
        targets = [{"path": str(path.resolve(strict=True)), "directory": path.is_dir()} for path in targets]
        with tempfile.TemporaryDirectory(prefix="aster-lab-import-") as temporary:
            imports = capture_json(IMPORT_PROBE, [str(source)], Path(temporary), env, redactor)
            write_json(result / "imports.json", imports, redactor)
            probe = capture_json(ISOLATION_PROBE, [json.dumps(targets)], Path(temporary), env, redactor)
        write_json(result / "isolation-probe.json", probe, redactor)
        print(json.dumps({"result": str(result), "passed": probe["passed"]}))
        return 0 if probe["passed"] else 1
    imports = capture_json(IMPORT_PROBE, [str(source), str(args.env_file.resolve()),
                           str(args.profile_config.resolve()), args.profile], workspace, env, redactor)
    if args.resume:
        checkpoint = json.loads((workspace / ".aster/runs" / args.resume / "checkpoint.json").read_text())
        if checkpoint["runtime_identity"] != imports["settings"]:
            parser.error("resume configuration identity differs from saved runtime")
    write_json(result / "imports.json", imports, redactor)
    launch = {"python": str(PYTHON), "executable": str(PYTHON.parent / "aster"),
              "workspace": str(workspace), "source_sha256": before["sha256"],
              "prompt_sha256": sha(prompt.encode()), "profile": args.profile,
              "profile_config_sha256": sha(args.profile_config.read_bytes()),
              "budget": {"steps": args.steps, "turns": args.turns, "seconds": args.seconds},
              "answer_policy": {"default": ANSWER, "max_clarifications": MAX_CLARIFICATIONS},
              "initial_workspace_empty": not any(workspace.iterdir()),
              "resume": args.resume, "excluded_prior_run_ids": sorted(initial_runs),
              "allow_source_change": args.allow_source_change,
              "previous_source_sha256": prior["source_sha256"] if args.resume else None}
    write_json(result / "launch.json", launch, redactor)
    command = [str(PYTHON), str(PYTHON.parent / "aster"), "--cwd", str(workspace),
               "--env-file", str(args.env_file.resolve()), "--config", str(args.profile_config.resolve()),
               "--profile", args.profile, "--max-steps", str(args.steps), "--max-turns", str(args.turns)]
    if args.resume:
        command.extend(["--resume", args.resume])
    if prompt:
        command.append(prompt)
    summary = drive(command, workspace, env, result, args.seconds, redactor, initial_runs)
    summary.update(launch)
    summary["runs"] = collect_runs(workspace, result, redactor, initial_runs)
    after = manifest(source)
    summary["source_unchanged"] = before == after
    summary["source_sha256_after"] = after["sha256"]
    write_json(result / "summary.json", summary, redactor)
    print(json.dumps(redactor.value({key: summary[key] for key in
          ("exit_code", "elapsed_seconds", "driver_stop_reason", "source_unchanged", "runs")}), ensure_ascii=False))
    return 0 if summary["exit_code"] == 0 and summary["source_unchanged"] and not summary["reader_errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
