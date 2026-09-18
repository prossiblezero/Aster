#!/usr/bin/env python3
"""Aggregate category counts from a JSON Lines file."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import NoReturn, Sequence


class InputError(Exception):
    """Raised when the input file does not satisfy the data contract."""


def _reject_json_constant(value: str) -> NoReturn:
    raise ValueError(f"invalid JSON constant {value}")


def aggregate(path: Path) -> dict[str, object]:
    categories: dict[str, int] = {}
    records = 0
    total = 0

    if not path.is_file():
        raise InputError(f"input path '{path}' is not a regular file")

    try:
        with path.open("r", encoding="utf-8") as input_file:
            for line_number, line in enumerate(input_file, start=1):
                if not line.strip():
                    continue

                try:
                    record = json.loads(line, parse_constant=_reject_json_constant)
                except (json.JSONDecodeError, ValueError) as error:
                    raise InputError(f"line {line_number}: invalid JSON ({error})") from error

                if not isinstance(record, dict):
                    raise InputError(f"line {line_number}: record must be a JSON object")
                if "category" not in record:
                    raise InputError(f"line {line_number}: missing 'category' field")
                if "count" not in record:
                    raise InputError(f"line {line_number}: missing 'count' field")

                category = record["category"]
                count = record["count"]
                if not isinstance(category, str) or not category.strip():
                    raise InputError(
                        f"line {line_number}: 'category' must be a nonempty string"
                    )
                if type(count) is not int or count < 0:
                    raise InputError(
                        f"line {line_number}: 'count' must be an integer greater than or equal to zero"
                    )

                key = category.strip()
                categories[key] = categories.get(key, 0) + count
                records += 1
                total += count
    except (OSError, UnicodeError) as error:
        raise InputError(f"cannot read input file '{path}': {error}") from error

    return {
        "categories": [
            {"category": category, "count": categories[category]}
            for category in sorted(categories)
        ],
        "records": records,
        "total": total,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="eventcount.py",
        description="Aggregate category counts from a UTF-8 JSON Lines file.",
    )
    parser.add_argument("INPUT", type=Path, help="UTF-8 JSON Lines input file")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = aggregate(args.INPUT)
    except InputError as error:
        print(f"eventcount.py: error: {error}", file=sys.stderr)
        return 2

    json.dump(result, sys.stdout, ensure_ascii=False, separators=(",", ":"))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
