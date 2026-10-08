"""Fetch frozen base repositories, keeping benchmark answers in the private controller."""
from __future__ import annotations

import hashlib
import json
import shutil
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
PRIVATE = REPO / "artifacts/private" / HERE.name


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def fetch(row):
    url = f"https://codeload.github.com/{row['repo']}/tar.gz/{row['base_commit']}"
    destination = PRIVATE / "archives" / (row["instance_id"] + ".tar.gz")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        with urllib.request.urlopen(url, timeout=120) as response:
            temporary = destination.with_suffix(".download")
            with temporary.open("wb") as output:
                shutil.copyfileobj(response, output)
            temporary.replace(destination)
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    target = PRIVATE / "objects" / digest
    target.parent.mkdir(exist_ok=True)
    if not target.exists():
        target.hardlink_to(destination)
    return {
        "id": row["instance_id"], "upstream_id": row["instance_id"],
        "source": "swebench-verified", "difficulty": row["aster_difficulty"],
        "prompt": row["problem_statement"], "origin": {k: row[k] for k in ("repo", "base_commit", "version")},
        "grading": {"reference_sha256": row["patch_sha256"], "test_patch_sha256": row["test_patch_sha256"],
                    "eval_script_sha256": row["eval_script_sha256"]},
        "materials": {"setup": [{"path": "base.tar.gz", "asset": digest, "url": url}]},
    }


def main():
    manifest = json.loads((HERE / "manifest.json").read_text())
    with ThreadPoolExecutor(max_workers=3) as pool:
        tasks = list(pool.map(fetch, manifest["instances"]))
    save(HERE / "tasks.json", tasks)
    parser = REPO / "artifacts/private/worker-code-20260928/official_log_parsers.py"
    shutil.copy2(parser, PRIVATE / parser.name)
    print(f"Prepared {len(tasks)} frozen source archives.", flush=True)


if __name__ == "__main__":
    main()
