"""Rebuild the source cache from public commit URLs, verifying every SHA-256."""
from __future__ import annotations

import hashlib
import json
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "artifacts/private/subagent-ten-20261003/upstream"


def fetch(item):
    path, url, expected = item
    data = path.read_bytes() if path.exists() else urllib.request.urlopen(url, timeout=120).read()
    if hashlib.sha256(data).hexdigest() != expected:
        raise ValueError("source_hash_mismatch: " + str(path))
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return str(path.relative_to(REPO))


def main():
    tasks = json.loads((REPO / "datasets/subagent-ten-20261003/instances.json").read_text())
    original = json.loads((REPO / "datasets/subagent-light-20260930/manifest.json").read_text())
    items = [(REPO / "artifacts/private/worker-code-v2/objects" / sha, row["url"], sha)
             for sha, row in original["asset_metadata"].items()]
    OUT.mkdir(parents=True, exist_ok=True)
    for task in tasks:
        if "upstream" not in task:
            continue
        metadata = task["upstream"]
        path = OUT / (task["project"] + ".json")
        if path.exists():
            assert json.loads(path.read_text()) == metadata
        else:
            path.write_text(json.dumps(metadata, indent=2) + "\n")
        items.extend((OUT / task["project"] / row["path"], row["url"], row["sha256"])
                     for row in metadata["files"])
    with ThreadPoolExecutor(max_workers=4) as pool:
        for path in pool.map(fetch, items):
            print("verified", path, flush=True)


if __name__ == "__main__":
    main()
