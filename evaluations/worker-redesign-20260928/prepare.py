"""Prepare immutable source objects and the one historical stdlib compatibility copy."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DATA = REPO / "datasets/worker-code-v3-holdout"
PRIVATE = REPO / "artifacts/private/worker-redesign-20260928"
CACHE = PRIVATE / "objects"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fetch", action="store_true", help="Download missing pinned assets; never run a model")
    parser.add_argument("--pyarrow-root", type=Path, help="Optional external pyarrow installation for parquet extraction")
    args = parser.parse_args()
    if sys.version_info[:2] != (3, 12) or "aster" not in sys.prefix:
        raise RuntimeError("use_conda_aster_or_derived_python312")
    CACHE.mkdir(parents=True, exist_ok=True)
    for dataset in (REPO / "datasets/worker-code-v2", DATA):
        manifest = json.loads((dataset / "manifest.json").read_text())
        for filename, expected in manifest["file_sha256"].items():
            if digest((dataset / filename).read_bytes()) != expected:
                raise ValueError("changed_frozen_dataset: " + filename)
        lock = json.loads((dataset / "sources.json").read_text())
        for sha, item in lock["assets"].items():
            path = CACHE / sha
            old = REPO / "artifacts/private/worker-code-v2/objects" / sha
            if not path.exists() and old.exists():
                path.hardlink_to(old)
            if not path.exists():
                if not args.fetch:
                    raise FileNotFoundError("missing object; run --fetch: " + sha)
                content = urllib.request.urlopen(item["url"], timeout=60).read()
                if len(content) != item["bytes"] or digest(content) != sha:
                    raise ValueError("download_checksum_mismatch: " + sha)
                path.write_bytes(content)
            if len(path.read_bytes()) != item["bytes"] or digest(path.read_bytes()) != sha:
                raise ValueError("cache_checksum_mismatch: " + sha)
        for sha, item in lock.get("derived_assets", {}).items():
            path = CACHE / sha
            if not path.exists():
                if args.pyarrow_root:
                    sys.path.insert(0, str(args.pyarrow_root.resolve()))
                import pyarrow.parquet as pq
                rows = pq.read_table(CACHE / item["source_asset"]).to_pylist()
                row = next(row for row in rows if row["instance_id"] == item["instance_id"])
                content = (json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n").encode()
                if digest(content) != sha:
                    raise ValueError("derived_record_checksum_mismatch")
                path.write_bytes(content)
            if digest(path.read_bytes()) != sha:
                raise ValueError("changed_derived_record")
    metadata = json.loads((DATA / "environment.json").read_text())
    root = Path(metadata["runtime"])
    dependency = root / "lib/python3.9/urllib/parse.py"
    if not dependency.exists() or digest(dependency.read_bytes()) != metadata["sha256"]:
        if not args.fetch:
            raise FileNotFoundError("historical urllib runtime absent; run --fetch")
        content = urllib.request.urlopen(metadata["source"], timeout=60).read()
        if digest(content) != metadata["sha256"]:
            raise ValueError("stdlib_checksum_mismatch")
        if not root.exists():
            shutil.copytree(metadata["derived_from"], root, copy_function=os.link, symlinks=True)
        # Unlink first: the rest of the clone shares read-only dependency inodes.
        dependency.unlink()
        dependency.write_bytes(content)
        for bytecode in (dependency.parent / "__pycache__").glob("parse.*.pyc"):
            bytecode.unlink()
    (PRIVATE / "holdout-environment.json").write_text(json.dumps(metadata, indent=2) + "\n")
    print("All frozen objects and historical urllib dependency verified; no model calls.")


if __name__ == "__main__":
    main()
