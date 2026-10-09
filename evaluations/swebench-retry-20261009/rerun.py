"""Run the five authorized infrastructure retries with the frozen original driver."""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import importlib.util
import json
import os
from pathlib import Path
from socketserver import ThreadingMixIn, UnixStreamServer

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
PRIVATE = REPO / "artifacts/private" / HERE.name
ORIGINAL = HERE.parent / "swebench-compare-20261008"
JOBS = (
    ("aster", "sphinx-doc__sphinx-9658"),
    ("pi", "django__django-15957"),
    ("codex", "django__django-15382"),
    ("aster", "sympy__sympy-13031"),
    ("pi", "sympy__sympy-18199"),
)


def original_module(name):
    spec = importlib.util.spec_from_file_location(name, ORIGINAL / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.PRIVATE = PRIVATE
    return module


def check_sources():
    manifest = json.loads((ORIGINAL / "manifest.json").read_text())
    for name, digest in manifest["aster_source_hashes"].items():
        assert hashlib.sha256((REPO / name).read_bytes()).hexdigest() == digest, name
        frozen = PRIVATE / "runtime" / name.removeprefix("src/")
        assert hashlib.sha256(frozen.read_bytes()).hexdigest() == digest, name
    for name, digest in manifest["evaluation_source_hashes"].items():
        assert hashlib.sha256((ORIGINAL / name).read_bytes()).hexdigest() == digest, name


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("gateway", "run"))
    args = parser.parse_args()
    check_sources()
    if args.mode == "gateway":
        gateway = original_module("gateway")
        assert gateway.SETTINGS.model == "gpt-5.6-luna"

        class Server(ThreadingMixIn, UnixStreamServer):
            daemon_threads = True

        with Server(str(PRIVATE / "relay/model.sock"), gateway.Gateway) as server:
            print("Retry model gateway ready", flush=True)
            server.serve_forever()
    else:
        driver = original_module("compare")
        assert json.loads((PRIVATE / "preflight.json").read_text())["passed"]
        # A fixed queue and the original driver's partial-run guard prevent extra attempts.
        rows = {row["instance_id"]: row for row in driver.DATASET}
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures = []
            for agent, instance in JOBS:
                previous = REPO / "artifacts/private" / ORIGINAL.name / "runs" / f"{agent}-{instance}"
                assert json.loads((previous / "result.json").read_text())["patch_bytes"] == 0
                futures.append(pool.submit(driver.episode, agent, rows[instance]))
            for future in concurrent.futures.as_completed(futures):
                print(json.dumps({"finished": future.result()["job"], "load": os.getloadavg()}), flush=True)


if __name__ == "__main__":
    main()
