"""Retest the fixed Aster once on each of the original ten SWE-bench instances."""
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


def original_module(name):
    spec = importlib.util.spec_from_file_location(name, ORIGINAL / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.PRIVATE = PRIVATE
    return module


def check_sources():
    manifest = json.loads((HERE / "manifest.json").read_text())
    for name, digest in manifest["aster_source_hashes"].items():
        assert hashlib.sha256((REPO / name).read_bytes()).hexdigest() == digest, name
        frozen = PRIVATE / "runtime" / name.removeprefix("src/")
        assert hashlib.sha256(frozen.read_bytes()).hexdigest() == digest, name
    for name, digest in manifest["evaluation_source_hashes"].items():
        assert hashlib.sha256((ORIGINAL / name).read_bytes()).hexdigest() == digest, name
    for name, digest in manifest["runtime_asset_hashes"].items():
        assert hashlib.sha256((PRIVATE / "runtime" / name).read_bytes()).hexdigest() == digest, name
    assert hashlib.sha256((PRIVATE / "dataset.json").read_bytes()).hexdigest() == manifest["dataset_sha256"]
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("gateway", "run"))
    args = parser.parse_args()
    manifest = check_sources()
    if args.mode == "gateway":
        gateway = original_module("gateway")
        assert gateway.SETTINGS.model == manifest["model"]

        class Server(ThreadingMixIn, UnixStreamServer):
            daemon_threads = True

        with Server(str(PRIVATE / "relay/model.sock"), gateway.Gateway) as server:
            print("Verification retest model gateway ready", flush=True)
            server.serve_forever()
    else:
        driver = original_module("compare")
        assert json.loads((PRIVATE / "preflight.json").read_text())["passed"]
        rows = {row["instance_id"]: row for row in driver.DATASET}
        jobs = manifest["job_order"]
        assert len(jobs) == len(set(jobs)) == 10
        with concurrent.futures.ThreadPoolExecutor(max_workers=manifest["concurrency"]) as pool:
            futures = [pool.submit(driver.episode, "aster", rows[instance]) for instance in jobs]
            for future in concurrent.futures.as_completed(futures):
                print(json.dumps({"finished": future.result()["job"], "load": os.getloadavg()}), flush=True)


if __name__ == "__main__":
    main()
