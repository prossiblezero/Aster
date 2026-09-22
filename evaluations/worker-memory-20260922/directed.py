"""Four prespecified attempts for user-directed delegation; run after the main matrix."""

import argparse
import asyncio
import hashlib
import shutil
from pathlib import Path

import cases
import run as driver


async def main(output, prepared):
    output.mkdir(parents=True, exist_ok=False)
    shutil.copytree(prepared / "graders", output / "graders")
    driver.support.DATASETS = output / "graders"
    paths = ["cases.py", "run.py", "directed.py", "DIRECTED.md", "PROTOCOL.md"]
    driver.save(
        output / "freeze.json",
        {
            "source": driver.verify_source(),
            "commit": driver.SOURCE,
            "driver": {
                n: hashlib.sha256((driver.HERE / n).read_bytes()).hexdigest() for n in paths
            },
            "settings": driver.config().identity(),
            "limits": driver.LIMITS,
        },
    )
    rows = []
    suffix = (
        "\nFor this attempt, if Worker tools are available, the user explicitly requests "
        "that you delegate the two independent implementation modules to two Workers "
        "with disjoint write scopes. You should handle integration tests, wait for their "
        "results and verify the whole product. If Worker tools are unavailable, implement "
        "both modules yourself. Do not delegate writing the same tests file to both Workers."
    )
    for i, case in enumerate(cases.worker_cases()[:2]):
        for arm in ["solo", "directed"] if i == 0 else ["directed", "solo"]:
            name = f"directed-{case['id']}-{arm}"
            root = output / "workspaces" / name
            driver.put_files(root, case["files"])
            row = await driver.execute(
                name,
                root,
                driver.config(max_workers=0 if arm == "solo" else 2),
                case["prompt"] + suffix,
                output / "runs" / name,
                case["id"] + ".py",
            )
            row.update(family="directed", task=case["id"], arm=arm, category="independent")
            rows.append(row)
            driver.save(output / "results.json", rows)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--prepared", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(main(args.output.resolve(), args.prepared.resolve()))
