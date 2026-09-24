"""Two directed regression attempts using the frozen tasks, accounting and graders."""

import argparse
import asyncio
import hashlib
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = HERE.parent / "worker-memory-20260922"
sys.path.insert(0, str(BASE))
import cases  # noqa: E402
import run as driver  # noqa: E402


async def main(source, output, prepared):
    driver.SOURCE = source
    source_hashes = driver.verify_source()
    output.mkdir(parents=True, exist_ok=False)
    shutil.copytree(prepared / "graders", output / "graders")
    driver.support.DATASETS = output / "graders"
    paths = [
        BASE / name for name in ["cases.py", "run.py", "directed.py", "DIRECTED.md", "PROTOCOL.md"]
    ] + [HERE / "rerun.py", HERE / "PROTOCOL.md"]
    driver.save(
        output / "freeze.json",
        {
            "commit": source,
            "source": source_hashes,
            "driver": {
                str(p.relative_to(HERE.parent)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in paths
            },
            "graders": driver.hashes(output / "graders"),
            "settings": driver.config().identity(),
            "limits": driver.LIMITS,
        },
    )
    # Identical to the original directed.py suffix; no task-specific repair hint.
    suffix = (
        "\nFor this attempt, if Worker tools are available, the user explicitly requests "
        "that you delegate the two independent implementation modules to two Workers "
        "with disjoint write scopes. You should handle integration tests, wait for their "
        "results and verify the whole product. If Worker tools are unavailable, implement "
        "both modules yourself. Do not delegate writing the same tests file to both Workers."
    )
    rows = []
    for case in cases.worker_cases()[:2]:
        name = f"directed-{case['id']}-directed"
        root = output / "workspaces" / name
        driver.put_files(root, case["files"])
        row = await driver.execute(
            name,
            root,
            driver.config(max_workers=2),
            case["prompt"] + suffix,
            output / "runs" / name,
            case["id"] + ".py",
        )
        row.update(family="directed", task=case["id"], arm="directed", category="independent")
        rows.append(row)
        driver.save(output / "results.json", rows)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--prepared", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(main(args.source, args.output.resolve(), args.prepared.resolve()))
