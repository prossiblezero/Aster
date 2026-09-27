"""Validate the frozen dataset, optionally fetch assets and replay control oracles.

No task IDs, problem statements, tests or solutions live in this script.
This is dataset preparation, not an Aster model-evaluation runner.
"""
from __future__ import annotations

import argparse
import collections
import concurrent.futures
import gzip
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CACHE = ROOT.parents[1] / "artifacts/private/worker-code-v2/objects"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def safe_path(value: str) -> bool:
    path = PurePosixPath(value)
    return bool(value) and not path.is_absolute() and not ({"..", ".git"} & set(path.parts))


def load() -> tuple[list[dict], dict]:
    manifest = json.loads((ROOT / "manifest.json").read_text())
    for filename, expected in manifest["file_sha256"].items():
        require(safe_path(filename), "unsafe manifest path")
        require(digest((ROOT / filename).read_bytes()) == expected, f"changed frozen file: {filename}")
    tasks = [json.loads(line) for line in (ROOT / "instances.jsonl").read_text().splitlines()]
    lock = json.loads((ROOT / "sources.json").read_text())
    require(len(tasks) == manifest["total"], "wrong task count")
    require(len({t["id"] for t in tasks}) == len(tasks), "duplicate task id")
    require(len({(t["source"], t["upstream_id"]) for t in tasks}) == len(tasks), "duplicate source task")
    for field in ["difficulty", "source"]:
        actual = dict(collections.Counter(t[field] for t in tasks))
        require(actual == manifest[field + "_counts"], f"wrong {field} distribution")
    for h, a in lock["assets"].items():
        require(re.fullmatch(r"[a-f0-9]{64}", h) is not None, "invalid asset hash")
        require(a["bytes"] > 0, "empty source asset")
        require(a["url"].startswith("https://") and re.search(r"/[a-f0-9]{40}(?:/|$)", a["url"]) is not None,
                f"source URL is not pinned: {a['url']}")
    for t in tasks:
        require(t["source"] in lock["benchmarks"], f"missing source: {t['id']}")
        require(t["split"] == "evaluation", "non-evaluation task")
        require(digest(t["prompt"].encode()) == t["prompt_sha256"], f"changed prompt: {t['id']}")
        require(t["collaboration"]["annotation_is_model_input"] is False, "audit annotation leaks into prompt")
        for role, files in t["materials"].items():
            for f in files:
                require(f["asset"] in lock["assets"], "missing asset")
                require(safe_path(f["path"]), "unsafe material path")
                if role == "public":
                    require(not ({".meta", "solution", "cheat"} & set(PurePosixPath(f["path"]).parts)), "reference file in public workspace")
        for field in ["filename", "test_filename"]:
            if field in t["grading"]:
                require(safe_path(t["grading"][field]), "unsafe control filename")
    return tasks, lock


def cache_asset(item: tuple[str, dict], cache: Path, fetch: bool) -> str:
    h, info = item
    target = cache / h
    if target.exists():
        data = target.read_bytes()
    else:
        require(fetch, f"missing asset {h}; use --fetch")
        request = urllib.request.Request(info["url"], headers={"User-Agent": "Aster-dataset-preparation/2"})
        with urllib.request.urlopen(request, timeout=60) as response:
            data = response.read()
    require(len(data) == info["bytes"] and digest(data) == h, f"source checksum mismatch: {info['url']}")
    if not target.exists():
        cache.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=cache, delete=False) as f:
            f.write(data)
            tmp = Path(f.name)
        tmp.replace(target)
    return h


def control_replay(task: dict, cache: Path) -> dict:
    g = task["grading"]
    if g["adapter"] == "aider_unittest":
        starter, reference, tests = [(cache / g[k]).read_text() for k in ["starter", "reference", "tests"]]
        filename, test_filename = g["filename"], g["test_filename"]
    else:
        with gzip.open(cache / g["record_archive"], "rt") as f:
            rows = [json.loads(line) for line in f]
        row = next(r for r in rows if r["task_id"] == g["row_id"])
        for key, h in g["field_sha256"].items():
            require(digest(row[key].encode()) == h, f"changed BigCodeBench field: {key}")
        starter = row["code_prompt"] + "    pass\n"
        reference = row["complete_prompt"] + row["canonical_solution"]
        tests = "from solution import task_func\n" + row["test"]
        filename, test_filename = "solution.py", "test_solution.py"
    results = {}
    for arm, code in [("starter", starter), ("reference", reference)]:
        with tempfile.TemporaryDirectory(prefix="aster-worker-control-") as directory:
            work = Path(directory)
            (work / filename).write_text(code)
            (work / test_filename).write_text(tests)
            env = dict(os.environ, PYTHONPATH="", PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
            run = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", ".", "-p", test_filename],
                                 cwd=work, env=env, capture_output=True, text=True, timeout=g["timeout_seconds"])
            output = run.stdout + run.stderr
            count = re.search(r"Ran (\d+) tests?", output)
            require(count is not None and int(count[1]) == g["expected_tests"], f"wrong collected count: {task['id']} {arm}")
            require((run.returncode == 0) == (arm == "reference"), f"unexpected {arm} result: {task['id']}\n{output}")
            results[arm] = {"returncode": run.returncode, "tests": int(count[1]), "output_tail": output[-800:]}
    return {"id": task["id"], "mode": "immutable_upstream_unittest", **results}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fetch", action="store_true", help="download missing pinned assets; do not execute setup scripts")
    parser.add_argument("--check-cache", action="store_true", help="verify all local source objects, without network")
    parser.add_argument("--preflight-controls", action="store_true", help="run starter/reference checks for the two lightweight adapters")
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--report", type=Path, help="write control replay results, for example under artifacts/private/")
    args = parser.parse_args()
    tasks, lock = load()
    if args.fetch or args.check_cache:
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(lambda item: cache_asset(item, args.cache, args.fetch), lock["assets"].items()))
        print(f"verified {len(lock['assets'])} cached source assets")
    if args.preflight_controls:
        controls = [t for t in tasks if t["grading"]["adapter"] in {"aider_unittest", "bigcode_unittest"}]
        require(bool(controls), "no control tasks")
        # Verify objects before replay, even when --check-cache was not requested.
        refs = set()
        for t in controls:
            g = t["grading"]
            refs.update(g[k] for k in ["starter", "reference", "tests", "record_archive"] if k in g)
        for h in refs:
            cache_asset((h, lock["assets"][h]), args.cache, False)
        results = [control_replay(t, args.cache) for t in controls]
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps({"python": sys.executable, "results": results}, ensure_ascii=False, indent=2) + "\n")
        print(f"control oracles: {len(results)} starter failures, {len(results)} reference passes, "
              f"{sum(r['reference']['tests'] for r in results)} reference tests")
    print(f"valid: {len(tasks)} tasks, {len(lock['benchmarks'])} sources, "
          f"difficulty={dict(collections.Counter(t['difficulty'] for t in tasks))}")


if __name__ == "__main__":
    main()
