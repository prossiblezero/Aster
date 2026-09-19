"""Run the public Aster CLI on the frozen tutorial maintenance workspace."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[1]
SPEC = importlib.util.spec_from_file_location(
    "recorded_cli_driver", BASE.parent / "lab-followup-20260919/run_pair.py"
)
driver = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(driver)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True)
    parser.add_argument("--prompt", type=Path, default=BASE / "task.md")
    parser.add_argument("--resume")
    parser.add_argument("--steps", type=int, default=140)
    parser.add_argument("--turns", type=int, default=90)
    parser.add_argument("--seconds", type=float, default=1800)
    args = parser.parse_args()
    if not args.name.replace("-", "").isalnum():
        parser.error("name must contain only letters, digits and hyphens")
    source, workspace = BASE / "source", BASE / "workspace"
    config, credentials = BASE / "private.toml", ROOT / ".env"
    before = driver.manifest(source)
    redactor = driver.Redactor(credentials, config)
    env = driver.child_environment(source)
    result = BASE / "runs" / args.name
    result.mkdir(parents=True, exist_ok=False)
    imports = driver.capture_json(driver.IMPORT_PROBE, [
        str(source), str(credentials), str(config), "main"
    ], workspace, env, redactor)
    if args.resume:
        saved = json.loads((workspace / ".aster/runs" / args.resume / "checkpoint.json").read_text())
        if saved["runtime_identity"] != imports["settings"]:
            raise ValueError("resume runtime identity changed")
    prior = {path.name for path in (workspace / ".aster/runs").glob("*")}
    prompt = args.prompt.read_text()
    launch = {"source": before, "imports": imports, "prompt_sha256": driver.sha(prompt.encode()),
              "resume": args.resume, "workspace": str(workspace),
              "budget": {"steps": args.steps, "turns": args.turns, "seconds": args.seconds},
              "private_config_sha256": driver.sha(config.read_bytes())}
    driver.write_json(result / "launch.json", launch, redactor)
    command = [str(driver.PYTHON), str(driver.PYTHON.parent / "aster"), "--cwd", str(workspace),
               "--env-file", str(credentials), "--config", str(config), "--profile", "main",
               "--max-steps", str(args.steps), "--max-turns", str(args.turns)]
    if args.resume:
        command += ["--resume", args.resume]
    command.append(prompt)
    summary = driver.drive(command, workspace, env, result, args.seconds, redactor, prior)
    summary.update(launch)
    summary["runs"] = driver.collect_runs(workspace, result, redactor, prior)
    summary["source_unchanged"] = driver.manifest(source) == before
    driver.write_json(result / "summary.json", summary, redactor)
    print(json.dumps(redactor.value({k: summary[k] for k in [
        "exit_code", "elapsed_seconds", "driver_stop_reason", "source_unchanged", "runs"
    ]}), ensure_ascii=False))
    return 0 if summary["exit_code"] == 0 and summary["source_unchanged"] else 1


if __name__ == "__main__":
    sys.exit(main())
