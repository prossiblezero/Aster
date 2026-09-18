#!/usr/bin/env python3
"""Merge and validate tags from JSON files in one directory."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


class TagMergeError(Exception):
    """An input cannot be processed according to the tagmerge format."""


def _reject_nonstandard_constant(value: str) -> Any:
    raise ValueError(f"invalid JSON constant: {value}")


def _load_file(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as source:
            return json.load(source, parse_constant=_reject_nonstandard_constant)
    except UnicodeDecodeError as exc:
        raise TagMergeError(f"{path}: not valid UTF-8") from exc
    except (json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise TagMergeError(f"{path}: invalid JSON") from exc
    except OSError as exc:
        raise TagMergeError(f"{path}: cannot read file") from exc


def merge_directory(directory: Path) -> dict[str, list[str]]:
    if not directory.exists():
        raise TagMergeError(f"{directory}: directory does not exist")
    if not directory.is_dir():
        raise TagMergeError(f"{directory}: not a directory")

    merged: dict[str, set[str]] = {}
    try:
        files = sorted(
            path for path in directory.iterdir() if path.is_file() and path.suffix == ".json"
        )
    except OSError as exc:
        raise TagMergeError(f"{directory}: cannot read directory") from exc

    for path in files:
        records = _load_file(path)
        if not isinstance(records, list):
            raise TagMergeError(f"{path}: top level must be an array")

        for index, record in enumerate(records):
            location = f"{path}: record {index}"
            if not isinstance(record, dict):
                raise TagMergeError(f"{location} must be an object")

            item_id = record.get("id")
            if not isinstance(item_id, str):
                raise TagMergeError(f"{location} field 'id' must be a string")
            item_id = item_id.strip()
            if not item_id:
                raise TagMergeError(f"{location} field 'id' must not be blank")

            tags = record.get("tags")
            if not isinstance(tags, list):
                raise TagMergeError(f"{location} field 'tags' must be an array")

            clean_tags: list[str] = []
            for tag_index, tag in enumerate(tags):
                if not isinstance(tag, str):
                    raise TagMergeError(
                        f"{location} tag {tag_index} must be a string"
                    )
                tag = tag.strip()
                if not tag:
                    raise TagMergeError(f"{location} tag {tag_index} must not be blank")
                clean_tags.append(tag)

            merged.setdefault(item_id, set()).update(clean_tags)

    return {item_id: sorted(merged[item_id]) for item_id in sorted(merged)}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tagmerge.py",
        description="Merge tags from JSON files directly contained in DATA_DIR.",
    )
    parser.add_argument("DATA_DIR", type=Path, help="directory containing JSON files")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        result = merge_directory(args.DATA_DIR)
    except TagMergeError as exc:
        print(f"tagmerge.py: error: {exc}", file=sys.stderr)
        return 2

    json.dump(result, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
