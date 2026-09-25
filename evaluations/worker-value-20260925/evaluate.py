"""Paired repository investigations using the existing real-model ledger and grader."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
BASE = HERE.parent / "worker-memory-20260922"
sys.path.insert(0, str(BASE))
import run as driver  # noqa: E402

import aster  # noqa: E402

spec = importlib.util.spec_from_file_location("value_cases", HERE / "cases.py")
case_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(case_module)

SUFFIX = (
    "\nIf Worker tools are available, delegate the three independent subsystem "
    "investigations to three Explore workers with self-contained assignments. "
    "You own the final report and checking that their evidence answers the questions. "
    "If Worker tools are unavailable, investigate all three yourself. "
    "Use any available tools efficiently; no need to write tests for this audit."
)


def verify_source(source, source_root):
    assert (
        Path(sys.executable).resolve() == Path("/home/dzh/.conda/envs/aster/bin/python").resolve()
    )
    assert Path(aster.__file__).resolve() == source_root / "src/aster/__init__.py"
    assert not subprocess.check_output(["git", "diff", source, "--", "src/aster"], cwd=source_root)
    return driver.hashes(source_root / "src/aster", "**/*.py")


async def preflight(output):
    output.mkdir(parents=True, exist_ok=False)
    graders = output / "graders"
    graders.mkdir()
    driver.support.DATASETS = graders
    rows = []
    for case in case_module.cases():
        grader = case["id"] + ".py"
        (graders / grader).write_text(case["grader"])
        with tempfile.TemporaryDirectory(prefix="aster-worker-value-preflight-") as d:
            root = Path(d)
            driver.put_files(root, case["files"])
            missing = await driver.support.external_check(root, grader)
            driver.save(root / "report.json", case["reference_report"])
            good = await driver.support.external_check(root, grader)
            driver.save(root / "report.json", {})
            wrong = await driver.support.external_check(root, grader)
            driver.save(root / "report.json", case["reference_report"])
            target = next(k for k in case["files"] if k.endswith(".py"))
            (root / target).write_text("# Altered source.\n")
            modified = await driver.support.external_check(root, grader)
            passed = good["passed"] and not any(r["passed"] for r in [missing, wrong, modified])
            rows.append(
                {
                    "id": case["id"],
                    "passed": passed,
                    "missing": missing,
                    "reference": good,
                    "wrong": wrong,
                    "modified": modified,
                }
            )
            print(case["id"], passed, flush=True)
    driver.save(output / "preflight.json", rows)
    assert all(r["passed"] for r in rows)


async def execute(args):
    source_root = args.source_root.resolve()
    driver.SOURCE = args.source
    driver.verify_source = lambda: verify_source(args.source, source_root)
    source_hashes = driver.verify_source()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    prepared = args.prepared.resolve()
    checks = json.loads((prepared / "preflight.json").read_text())
    assert len(checks) == 2 and all(c["passed"] for c in checks)
    shutil.copytree(prepared / "graders", output / "graders")
    driver.support.DATASETS = output / "graders"
    paths = [
        HERE / "evaluate.py",
        HERE / "cases.py",
        HERE / args.protocol,
        BASE / "run.py",
        REPO / "scripts/benchmark_support.py",
    ]
    driver.save(
        output / "freeze.json",
        {
            "source": args.source,
            "driver_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip(),
            "source_root": str(source_root),
            "source_hashes": source_hashes,
            "drivers": {
                str(p.relative_to(REPO)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths
            },
            "graders": driver.hashes(output / "graders"),
            "limits": driver.LIMITS,
            "settings": {
                arm: driver.config(max_workers=0 if arm == "solo" else 3).identity()
                for arm in args.arms
            },
            "plan": vars(args) | {k: str(v) for k, v in vars(args).items() if isinstance(v, Path)},
        },
    )
    rows = []
    for repetition in range(args.repetitions):
        for case in case_module.cases():
            if case["id"] not in args.cases:
                continue
            arms = args.arms if repetition % 2 == 0 else list(reversed(args.arms))
            for arm in arms:
                name = f"{case['id']}-{arm}-r{repetition + 1}"
                root = output / "workspaces" / name
                driver.put_files(root, case["files"])
                row = await driver.execute(
                    name,
                    root,
                    driver.config(max_workers=0 if arm == "solo" else 3),
                    case["prompt"] + SUFFIX,
                    output / "runs" / name,
                    case["id"] + ".py",
                    learning=True,
                )
                row.update(task=case["id"], arm=arm, repetition=repetition + 1)
                rows.append(row)
                driver.save(output / "results.json", rows)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--prepared", type=Path)
    parser.add_argument("--source")
    parser.add_argument("--source-root", type=Path, default=REPO)
    parser.add_argument("--cases", nargs="+", default=["retention-audit"])
    parser.add_argument(
        "--arms", nargs="+", choices=["solo", "workers"], default=["solo", "workers"]
    )
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--protocol", default="PROTOCOL.md")
    args = parser.parse_args()
    if args.preflight:
        asyncio.run(preflight(args.output.resolve()))
    else:
        assert args.source and args.prepared
        asyncio.run(execute(args))
