#!/usr/bin/env python3
"""Sum CSV amounts by category and emit JSON."""

from __future__ import annotations

import csv
import json
import sys
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
from pathlib import Path
from typing import TextIO


EXIT_INPUT_ERROR = 2


class InputError(Exception):
    """Raised when the input file cannot be processed."""


def _is_blank(row: list[str]) -> bool:
    return not row or all(field.strip() == "" for field in row)


def _add_exact(left: Decimal, right: Decimal) -> Decimal:
    """Add two finite Decimals with enough precision to avoid context rounding."""
    least_exponent = min(left.as_tuple().exponent, right.as_tuple().exponent)
    greatest_adjusted = max(left.adjusted(), right.adjusted())
    precision = max(1, greatest_adjusted - least_exponent + 2)
    with localcontext() as context:
        context.prec = precision
        return left + right


def _format_amount(value: Decimal) -> str:
    digits_before_point = max(1, value.adjusted() + 1)
    with localcontext() as context:
        context.prec = max(len(value.as_tuple().digits), digits_before_point + 2) + 2
        rounded = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return format(rounded, ".2f")


def calculate_totals(stream: TextIO) -> dict[str, str]:
    reader = csv.reader(stream, strict=True)

    try:
        header = next((row for row in reader if not _is_blank(row)), None)
    except csv.Error as error:
        raise InputError(f"invalid CSV: {error}") from error

    if header is None:
        raise InputError("missing header with category and amount columns")

    missing = [name for name in ("category", "amount") if name not in header]
    if missing:
        raise InputError(f"missing required column(s): {', '.join(missing)}")
    if header.count("category") > 1 or header.count("amount") > 1:
        raise InputError("duplicate category or amount column")

    category_index = header.index("category")
    amount_index = header.index("amount")
    totals: dict[str, Decimal] = {}

    try:
        for row_number, row in enumerate(reader, start=2):
            if _is_blank(row):
                continue

            category = row[category_index].strip() if category_index < len(row) else ""
            if not category:
                raise InputError(f"row {row_number}: category is empty")

            amount_text = row[amount_index].strip() if amount_index < len(row) else ""
            try:
                amount = Decimal(amount_text)
            except InvalidOperation as error:
                raise InputError(f"row {row_number}: invalid amount") from error
            if not amount.is_finite():
                raise InputError(f"row {row_number}: amount must be finite")

            totals[category] = _add_exact(totals.get(category, Decimal(0)), amount)
    except csv.Error as error:
        raise InputError(f"invalid CSV: {error}") from error

    return {category: _format_amount(totals[category]) for category in sorted(totals)}


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) != 1:
        print("usage: python csvtotals.py INPUT.csv", file=sys.stderr)
        return EXIT_INPUT_ERROR

    try:
        with Path(arguments[0]).open("r", encoding="utf-8-sig", newline="") as stream:
            result = calculate_totals(stream)
    except (OSError, UnicodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_INPUT_ERROR
    except InputError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_INPUT_ERROR

    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
