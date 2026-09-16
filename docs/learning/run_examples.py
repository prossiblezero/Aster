"""Run marked offline examples directly from the learning Markdown, without duplicate demos."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

LESSONS = Path(__file__).resolve().parent
ROOT = LESSONS.parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("chapter", nargs="?", default="all", help="Chapter number (00..15) or all")
    parser.add_argument("--list", action="store_true", help="Show examples without executing them")
    args = parser.parse_args()
    chapters = sorted(LESSONS.glob("[0-9][0-9]-*.md"))
    if args.chapter != "all":
        chapters = [path for path in chapters if path.name[:2] == args.chapter.zfill(2)]
    if not chapters:
        parser.error("unknown chapter; use 00..15 or all")
    count = 0
    for path in chapters:
        blocks = re.findall(r"^```python\n(.*?)^```\s*$", path.read_text(), re.M | re.S)
        examples = [block for block in blocks if block.startswith("# 可运行实验\n")]
        if not examples:
            parser.error(f"no marked examples in {path.name}")
        for number, source in enumerate(examples, 1):
            count += 1
            print(f"\n>>> {path.name} / {number}", flush=True)
            if not args.list:
                subprocess.run([sys.executable, "-c", source], cwd=ROOT, check=True, timeout=240)
    print(f"\n{count} examples {'listed' if args.list else 'passed'}.")


if __name__ == "__main__":
    main()
