#!/usr/bin/env python3
"""Aggregate category counts from a JSON Lines file."""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from collections import defaultdict
from typing import NoReturn, Optional


class InputError(Exception):
    """Raised when the input cannot be read or contains an invalid record."""


def _reject_json_constant(value: str) -> NoReturn:
    raise ValueError(f"invalid JSON constant {value!r}")


def summarize(path: str) -> dict[str, object]:
    """Read *path*, validate its records, and return the aggregate result."""
    try:
        file_status = os.stat(path)
    except OSError as exc:
        detail = exc.strerror or str(exc)
        raise InputError(f"cannot access input file {path!r}: {detail}") from None

    if not stat.S_ISREG(file_status.st_mode):
        raise InputError(f"input path {path!r} is not a regular file")

    category_counts: defaultdict[str, int] = defaultdict(int)
    records = 0
    total = 0

    try:
        with open(path, "rb") as input_file:
            for line_number, raw_line in enumerate(input_file, start=1):
                try:
                    line = raw_line.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise InputError(
                        f"invalid UTF-8 on line {line_number}: {exc.reason}"
                    ) from None

                if not line.strip():
                    continue

                try:
                    record = json.loads(line, parse_constant=_reject_json_constant)
                except (json.JSONDecodeError, ValueError) as exc:
                    raise InputError(f"invalid JSON on line {line_number}: {exc}") from None

                if not isinstance(record, dict):
                    raise InputError(f"invalid record on line {line_number}: expected an object")
                if "category" not in record:
                    raise InputError(
                        f"invalid record on line {line_number}: missing 'category' field"
                    )
                if "count" not in record:
                    raise InputError(
                        f"invalid record on line {line_number}: missing 'count' field"
                    )

                category = record["category"]
                count = record["count"]
                if not isinstance(category, str) or not category.strip():
                    raise InputError(
                        f"invalid record on line {line_number}: "
                        "'category' must be a nonempty string after stripping whitespace"
                    )
                if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                    raise InputError(
                        f"invalid record on line {line_number}: "
                        "'count' must be an integer greater than or equal to zero"
                    )

                category = category.strip()
                category_counts[category] += count
                records += 1
                total += count
    except InputError:
        raise
    except OSError as exc:
        detail = exc.strerror or str(exc)
        raise InputError(f"cannot read input file {path!r}: {detail}") from None

    categories = [
        {"category": category, "count": category_counts[category]}
        for category in sorted(category_counts)
    ]
    return {"categories": categories, "records": records, "total": total}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Aggregate counts by category from a UTF-8 JSON Lines file."
    )
    parser.add_argument("INPUT", help="path to the input JSON Lines file")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        result = summarize(args.INPUT)
    except InputError as exc:
        print(f"eventcount: error: {exc}", file=sys.stderr)
        return 2

    json.dump(result, sys.stdout, ensure_ascii=False, separators=(",", ":"))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
