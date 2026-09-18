#!/usr/bin/env python3
"""Count complete whitespace-delimited words in a UTF-8 text file."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Optional, Sequence


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="uniqwords.py",
        description="Count whitespace-delimited words in a UTF-8 text file.",
    )
    parser.add_argument("input", metavar="INPUT.txt", help="UTF-8 input file")
    return parser


def count_words(path: Path) -> dict[str, int]:
    text = path.read_text(encoding="utf-8")
    counts = Counter(text.split())
    return dict(sorted(counts.items()))


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        counts = count_words(Path(args.input))
    except UnicodeDecodeError:
        print(f"error: input is not valid UTF-8: {args.input}", file=sys.stderr)
        return 2
    except IsADirectoryError:
        print(f"error: input path is a directory: {args.input}", file=sys.stderr)
        return 2
    except FileNotFoundError:
        print(f"error: input file does not exist: {args.input}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"error: cannot read input file: {exc}", file=sys.stderr)
        return 2

    json.dump(counts, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
