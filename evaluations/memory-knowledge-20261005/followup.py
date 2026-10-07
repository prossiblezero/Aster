"""Two defect checks on the final source, separate from the frozen ten-task pair."""

import argparse
import asyncio
import copy
import hashlib
from pathlib import Path

import run


async def main(work):
    selected = [copy.deepcopy(case) for case in run.CASES
                if case["id"] in {"02_money", "07_repair_transfer"}]
    selected[1]["learn"] = [
        "修复 payments.py，使 test_payments.py 通过。先运行现有测试确认问题，再修复并验证。"
    ]
    out = run.HERE / "followup"
    run.save(out / "protocol.json", {
        "purpose": "Check explicit-request storage and optional learning after a verified repair.",
        "limitations": "Previously seen tasks; the repair prompt ensures a failing execution opportunity. "
                       "No remember instruction or seeded memories. Not part of the ten-task pair.",
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "cases": selected,
    })
    run.CASES = selected
    await run.main(argparse.Namespace(arm="knowledge", work=work,
                                     output=out / "results", case=None))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--work", type=Path, required=True)
    asyncio.run(main(parser.parse_args().work))
