"""Portable, secondary Terminal-Bench scoring from frozen dataset objects.

No model calls. Hidden tests/oracles are never copied into the agent workspace.
The host cannot setuid to nobody: fork/process isolation is retained, but this
is deliberately NOT the upstream root/nobody anti-tamper security boundary.
Run this file with Conda aster (or a venv derived from it) for the paired
starter/reference preflight. See each result's adaptations before aggregating.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))
from aster.safety.runner import SandboxRunner  # noqa: E402

ASTER_PYTHON = Path("/home/dzh/.conda/envs/aster/bin/python")
DERIVED_PYTHON = Path(os.environ.get(
    "ASTER_TERMINAL_PYTHON",
    "/tmp/aster-terminal-medium-preflight-20260927/kv-env/bin/python",
))
COUNTS = {
    "terminal-cancel-async-tasks": 6,
    "terminal-cobol-modernization": 3,
    "terminal-kv-store-grpc": 7,
    "terminal-mvcc-lsm-compaction": 15,
    "terminal-session-window-debug": 7,
}


def _asset(task: dict, cache: Path, path: str) -> bytes:
    items = [a for group in task["materials"].values() for a in group
             if a["path"] == path]
    if len(items) != 1:
        raise ValueError(f"Expected one frozen asset: {path}")
    digest = items[0]["asset"]
    source = cache / digest
    if not source.exists():
        source = cache / "objects" / digest
    data = source.read_bytes()
    if hashlib.sha256(data).hexdigest() != digest:
        raise ValueError(f"Frozen asset hash mismatch: {path}")
    return data


def _write(path: Path, data: bytes | str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        path.write_text(data)
    else:
        path.write_bytes(data)


def _python(task: dict) -> Path:
    if task["id"] in {"terminal-kv-store-grpc", "terminal-session-window-debug"}:
        if not DERIVED_PYTHON.is_file():
            raise FileNotFoundError(
                "Set ASTER_TERMINAL_PYTHON to a Conda-aster-derived venv with "
                "grpcio==1.73.0 grpcio-tools==1.73.0 protobuf==6.33.6 "
                "pytest-json-ctrf==0.5.2"
            )
        return DERIVED_PYTHON
    return ASTER_PYTHON


def _env(python: Path, candidate: Path) -> dict:
    env = SandboxRunner.environment(candidate.parent)
    Path(env["TMPDIR"]).mkdir(parents=True, exist_ok=True)
    env.update(PATH=f"{python.parent}:{env.get('PATH', '')}",
               PYTHONPATH=str(candidate), PYTHONDONTWRITEBYTECODE="1",
               PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
    return env


def _isolated(cmd: list[str], root: Path, cwd: Path, network: bool) -> list[str]:
    """Use current trusted Landlock implementation without exposing repo/cache.

    Signal delivery and process groups are necessary for the unchanged asyncio
    SIGINT tests and C++ subprocess harness. All other seccomp denials remain.
    gRPC alone enables sockets in the scorer (never in model tool execution).
    """
    launcher = root / "scoring_launcher.py"
    if not launcher.exists():
        source = (REPO / "src/aster/safety/linux.py").read_text()
        for name in ("setsid", "setpgid", "kill", "tkill", "tgkill"):
            source = source.replace(f'        "{name}",\n', "")
        source = source.rsplit('\nif __name__ == "__main__":', 1)[0]
        source += '''

if __name__ == "__main__":
    root, network, cwd, dependency, *command = sys.argv[1:]
    try:
        os.chdir(cwd)
        resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))
        resource.setrlimit(resource.RLIMIT_CPU, (120, 120))
        restrict(Path(root), network == "on", (Path(dependency), Path("/etc/passwd")))
    except Exception as exc:
        print(f"ASTER_SCORER_UNAVAILABLE:{type(exc).__name__}:{exc}", file=sys.stderr)
        sys.exit(125)
    os.execvpe(command[0], command, os.environ)
'''
        launcher.write_text(source)
    dependency = DERIVED_PYTHON.parent.parent if DERIVED_PYTHON.exists() else ASTER_PYTHON.parent.parent
    return [str(ASTER_PYTHON), "-I", str(launcher), str(root),
            "on" if network else "off", str(cwd), str(dependency), *cmd]


def _run(cmd: list[str], cwd: Path, env: dict, log: Path,
         timeout: int = 900) -> tuple[int, bool]:
    """Bound the whole process group, also when a test starts grandchildren."""
    with log.open("w") as stream:
        proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=stream,
                                stderr=subprocess.STDOUT, start_new_session=True)
        timed_out = False
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
        finally:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()
    return proc.returncode, timed_out


def _adaptations(task: dict) -> list[str]:
    result = [
        "Portable secondary score, not an official Terminal-Bench Docker result.",
        "Original /app maps to the workspace root; use relative task paths.",
        "Original hidden assertions and expected test counts are retained.",
        "Scoring uses a separate candidate copy and hidden tests outside the model workspace.",
        "Scorer uses Landlock, seccomp and a clean environment; repository/cache/credentials "
        "are not mounted. Signal/process-group syscalls are allowed for original subprocess tests.",
        "Host Python 3.12.14; original container CPU/memory limits are not reproduced.",
    ]
    if task["id"] in {"terminal-mvcc-lsm-compaction", "terminal-session-window-debug"}:
        result.append("Unprivileged host: nobody setuid/setgid calls become same-uid "
                      "execution; upstream root/nobody anti-tamper guarantee is unavailable.")
    if task["id"] == "terminal-mvcc-lsm-compaction":
        result.append("Compiler executables are recreated byte-for-byte with mode 0755 before "
                      "execution: seccomp blocks chmod to protect shared dependency metadata.")
    if task["id"] == "terminal-kv-store-grpc":
        result.append("Artifact replay: scorer launches submitted server.py on port 5328. "
                      "gRPC packages are preinstalled in a derived environment. "
                      "Cross-tool daemon persistence and package installation are not evaluated. "
                      "Sockets are enabled in this isolated scoring fixture, unlike model tools.")
    if task["id"] == "terminal-cobol-modernization":
        result.append("Original fixed-output grader is used; no live GnuCOBOL differential run.")
    return result


def prepare(task: dict, cache: Path, workspace: Path) -> dict:
    """Materialize public files only; workspace represents upstream /app."""
    workspace.mkdir(parents=True, exist_ok=True)
    for item in task["materials"]["public"]:
        path = item["path"]
        prefix = ("environment/app/" if task["id"] == "terminal-mvcc-lsm-compaction"
                  else "environment/")
        if not path.startswith(prefix):
            raise ValueError(f"Unexpected public path: {path}")
        _write(workspace / path.removeprefix(prefix), _asset(task, cache, path))
    python = _python(task)
    suffix = (
        "\nExecution environment: /app in the original task means the current workspace root. "
        "Use relative paths in code and commands; do not access the host /app. "
        f"Use {python} as the Python interpreter. Python 3.12 is available. "
        "Internet/package installation is unavailable during the task. "
        "The grader will evaluate an independent copy of your submitted files."
    )
    if task["id"] == "terminal-kv-store-grpc":
        suffix += (
            " grpcio==1.73.0, grpcio-tools==1.73.0 and protobuf==6.33.6 are preinstalled. "
            "Network listeners and background daemons cannot survive sandbox tool calls. "
            "Submit a server.py that starts the real service on 5328 when run; the scorer "
            "will launch it and test real loopback RPCs. You need not keep it alive between tools."
        )
    if task["id"] == "terminal-cobol-modernization":
        suffix += " GnuCOBOL is unavailable; read the supplied COBOL source and implement Python."
    return {"prompt_suffix": suffix, "execution_python": str(python),
            "adaptations": _adaptations(task)}


def _same_uid(source: str, *, python_source: bool) -> str:
    """Change only explicit privilege-drop calls, never tests/assertions."""
    if python_source:
        tree = ast.parse(source)
        lines = source.splitlines(keepends=True)
        for node in reversed(sorted(ast.walk(tree), key=lambda n: getattr(n, "lineno", 0))):
            if (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
                    and isinstance(node.value.func, ast.Attribute)
                    and isinstance(node.value.func.value, ast.Name)
                    and node.value.func.value.id == "os"
                    and node.value.func.attr in {"setgroups", "setgid", "setuid"}):
                lines[node.lineno - 1:node.end_lineno] = [" " * node.col_offset +
                    "pass  # portable: same uid; upstream nobody isolation unavailable\n"]
        return "".join(lines)
    return re.sub(r"(?m)^(\s*)os\.(setgroups|setgid|setuid)\([^\n]+\)$",
                  r"\1pass # portable: same uid", source)


def _junit(path: Path) -> dict:
    if not path.exists():
        return {"tests": 0, "passed_tests": 0, "failed_tests": 0, "errors": 0,
                "skipped_tests": 0, "cases": []}
    cases = []
    for node in ET.parse(path).getroot().iter("testcase"):
        status = next((tag for tag in ("error", "failure", "skipped")
                       if node.find(tag) is not None), "passed")
        cases.append({"name": node.attrib["name"], "status": status})
    return {"tests": len(cases), "passed_tests": sum(c["status"] == "passed" for c in cases),
            "failed_tests": sum(c["status"] == "failure" for c in cases),
            "errors": sum(c["status"] == "error" for c in cases),
            "skipped_tests": sum(c["status"] == "skipped" for c in cases), "cases": cases}


def grade(task: dict, cache: Path, workspace: Path, output: Path) -> dict:
    """Score all upstream tests, including hard-task integrity/storage gates."""
    output.mkdir(parents=True, exist_ok=True)
    destination = output.resolve()
    expected = COUNTS[task["id"]]
    result = {"task_id": task["id"], "adapter": "terminal_bench_portable",
              "mode": "terminal_bench_portable", "score_kind": "secondary", "expected_tests": expected,
              "adaptations": _adaptations(task)}
    started = time.monotonic()
    # No symlinks: avoid following a submitted pointer outside the task tree.
    links = [str(p.relative_to(workspace)) for p in workspace.rglob("*") if p.is_symlink()]
    if links:
        return dict(result, passed=False, status="invalid_submission", symlinks=links)
    with tempfile.TemporaryDirectory(prefix="aster-terminal-score-") as temp:
        root = Path(temp)
        output = root / "results"
        output.mkdir()
        candidate, hidden = root / "candidate", root / "hidden"
        shutil.copytree(workspace, candidate)
        hidden.mkdir()
        for item in task["materials"]["grader"]:
            if item["path"].startswith("tests/"):
                _write(hidden / item["path"].removeprefix("tests/"),
                       _asset(task, cache, item["path"]))
        python = _python(task)
        env = _env(python, candidate)
        config = hidden / "pytest.ini"
        config.write_text("[pytest]\n")
        if task["id"] == "terminal-mvcc-lsm-compaction":
            harness = hidden / "cxx_harness.py"
            source = harness.read_text().replace('APP = Path("/app")', f"APP = Path({str(candidate)!r})")
            # ld's final chmod is blocked by shared-dependency protection. Give
            # the identical executable bytes their mode at file creation instead.
            source = source.replace("    _open_ancestors(binary)", '''    _open_ancestors(binary)
    if not os.access(binary, os.X_OK):
        content = binary.read_bytes()
        binary.unlink()
        fd = os.open(binary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o755)
        with os.fdopen(fd, "wb") as output:
            output.write(content)''')
            harness.write_text(_same_uid(source, python_source=True))
            test = hidden / "test_visibility.py"
        else:
            test = hidden / "test_outputs.py"
            test.write_text(test.read_text().replace("/app", str(candidate)))
        if task["id"] == "terminal-cancel-async-tasks":
            shutil.copyfile(hidden / "test.py", candidate / "test.py")
        junit = output / "junit.xml"
        cmd = [str(python), "-m", "pytest", "-q", "-c", str(config),
               "--rootdir", str(hidden), "--confcutdir", str(hidden),
               "--basetemp", str(root / "pytest-temp"),
               f"--junitxml={junit}", str(test)]
        if task["id"] == "terminal-session-window-debug":
            conftest = hidden / "conftest.py"
            conftest.write_text(_same_uid(conftest.read_text(), python_source=True))
            shell = (hidden / "test.sh").read_text()
            # Keep baseline restoration, source scan, forked integrity check,
            # per-test fork, reward and CTRF count gate from the original script.
            shell = shell.replace("/app", str(candidate)).replace("/tests", str(hidden))
            shell = shell.replace("/logs/verifier", str(output / "verifier"))
            shell = _same_uid(shell, python_source=False)
            shell = shell.replace("python3 -m pytest --ctrf", "python3 -m pytest -p ctrf.main "
                f"-c {config} --rootdir {hidden} --confcutdir {hidden} "
                f"--basetemp {root / 'pytest-temp'} --junitxml={junit} --ctrf")
            script = hidden / "portable_test.sh"
            script.write_text(shell)
            cmd = ["bash", str(script)]
        server = None
        server_log = None
        try:
            if task["id"] == "terminal-kv-store-grpc":
                # Fixed upstream port; caller must serialize gRPC graders.
                with socket.socket() as probe:
                    try:
                        probe.bind(("127.0.0.1", 5328))
                    except OSError as exc:
                        return dict(result, passed=False, status="environment_error", error=str(exc))
                server_log = (output / "server.log").open("w")
                server_cmd = _isolated([str(python), "server.py"], root, candidate, True)
                server = subprocess.Popen(server_cmd, cwd=candidate, env=env,
                    stdout=server_log, stderr=subprocess.STDOUT, start_new_session=True)
                for _ in range(100):
                    if server.poll() is not None:
                        break
                    with socket.socket() as probe:
                        if probe.connect_ex(("127.0.0.1", 5328)) == 0:
                            break
                    time.sleep(0.05)
            cmd = _isolated(cmd, root, candidate, task["id"] == "terminal-kv-store-grpc")
            returncode, timed_out = _run(cmd, candidate, env, output / "grader.log")
        finally:
            if server is not None:
                try:
                    os.killpg(server.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                server.wait()
            if server_log:
                server_log.close()
        result.update(_junit(junit), returncode=returncode, timed_out=timed_out)
        reward_ok = True
        if task["id"] == "terminal-session-window-debug":
            reward = output / "verifier" / "reward.txt"
            reward_ok = reward.exists() and reward.read_text().strip() == "1"
            result["upstream_reward_gate"] = reward_ok
        result["passed"] = (returncode == 0 and not timed_out and reward_ok
                            and result["tests"] == expected
                            and result["passed_tests"] == expected)
        result["status"] = ("environment_error" if returncode == 125 else
                            "passed" if result["passed"] else "failed")
        result["wall_seconds"] = round(time.monotonic() - started, 3)
        _write(output / "result.json", json.dumps(result, indent=2) + "\n")
        shutil.copytree(output, destination, dirs_exist_ok=True)
        return result


def _reference(task: dict, cache: Path, workspace: Path, output: Path) -> None:
    """Apply frozen reference assets, never an implementation embedded here."""
    task_id = task["id"]
    if task_id == "terminal-session-window-debug":
        for item in task["materials"]["grader"]:
            if item["path"].startswith("solution/files/"):
                _write(workspace / "app" / Path(item["path"]).name,
                       _asset(task, cache, item["path"]))
    elif task_id == "terminal-mvcc-lsm-compaction":
        patch = output / "reference.patch"
        patch.write_bytes(_asset(task, cache, "solution/patch.diff"))
        subprocess.run(["patch", "-p1", "-i", str(patch.resolve())],
                       cwd=workspace, check=True, capture_output=True)
    else:
        shell = _asset(task, cache, "solution/solve.sh").decode()
        blocks = re.findall(r"cat > ([\w.-]+) << ['\"]?(\w+)['\"]?\n(.*?)\n\2(?:\n|$)",
                            shell, re.S)
        if not blocks:
            raise ValueError("No original oracle heredocs found")
        for name, _, contents in blocks:
            _write(workspace / name, contents + "\n")
        if task_id == "terminal-kv-store-grpc":
            subprocess.run([str(_python(task)), "-m", "grpc_tools.protoc", "-I.",
                            "--python_out=.", "--grpc_python_out=.", "kv-store.proto"],
                           cwd=workspace, check=True, capture_output=True)


def preflight(task: dict, cache: Path, output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="aster-terminal-preflight-") as temp:
        workspace = Path(temp) / "workspace"
        prepare(task, cache, workspace)
        starter = grade(task, cache, workspace, output / "starter")
        _reference(task, cache, workspace, output)
        reference = grade(task, cache, workspace, output / "reference")
    ready = (starter.get("tests") == COUNTS[task["id"]] and
             starter["status"] == "failed" and reference["passed"])
    return {"id": task["id"], "task_id": task["id"], "starter": starter,
            "reference": reference, "ready": ready, "preflight_passed": ready}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=Path("datasets/worker-code-v2/instances.jsonl"))
    parser.add_argument("--cache", type=Path, default=Path("artifacts/private/worker-code-v2/objects"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--task", action="append")
    args = parser.parse_args()
    results = []
    for line in args.dataset.read_text().splitlines():
        item = json.loads(line)
        if item["id"] not in COUNTS or args.task and item["id"] not in args.task:
            continue
        result = preflight(item, args.cache, args.output / item["id"])
        results.append(result)
        print(json.dumps(result), flush=True)
    _write(args.output / "preflight.json", json.dumps(results, indent=2) + "\n")
    raise SystemExit(0 if results and all(r["preflight_passed"] for r in results) else 1)
