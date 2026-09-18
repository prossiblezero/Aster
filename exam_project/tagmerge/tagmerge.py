#!/usr/bin/env python3
"""Merge tags for matching IDs from JSON files in one directory."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional


class TagMergeError(Exception):
    """An expected input or filesystem error."""


def parse_record(record: Any, source: Path, index: int) -> tuple[str, list[str]]:
    location = f"{source.name}: record {index}"
    if not isinstance(record, dict):
        raise TagMergeError(f"{location} must be an object")

    if "id" not in record or "tags" not in record:
        raise TagMergeError(f"{location} must contain id and tags")

    raw_id = record["id"]
    if not isinstance(raw_id, str):
        raise TagMergeError(f"{location} id must be a string")
    item_id = raw_id.strip()
    if not item_id:
        raise TagMergeError(f"{location} id must not be empty")

    raw_tags = record["tags"]
    if not isinstance(raw_tags, list):
        raise TagMergeError(f"{location} tags must be an array")

    tags: list[str] = []
    for tag_index, raw_tag in enumerate(raw_tags, start=1):
        if not isinstance(raw_tag, str):
            raise TagMergeError(
                f"{location} tag {tag_index} must be a string"
            )
        tag = raw_tag.strip()
        if not tag:
            raise TagMergeError(
                f"{location} tag {tag_index} must not be empty"
            )
        tags.append(tag)

    return item_id, tags


def load_file(path: Path) -> list[Any]:
    try:
        text = path.read_bytes().decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise TagMergeError(f"{path.name} is not valid UTF-8") from exc
    except OSError as exc:
        raise TagMergeError(f"cannot read {path.name}: {exc.strerror or exc}") from exc

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise TagMergeError(f"{path.name} is not valid JSON: {exc.msg}") from exc

    if not isinstance(data, list):
        raise TagMergeError(f"{path.name} top level must be an array")
    return data


def merge_directory(directory: Path) -> dict[str, list[str]]:
    if not directory.exists():
        raise TagMergeError(f"directory does not exist: {directory}")
    if not directory.is_dir():
        raise TagMergeError(f"not a directory: {directory}")

    try:
        files = sorted(
            path for path in directory.iterdir()
            if path.is_file() and path.suffix == ".json"
        )
    except OSError as exc:
        raise TagMergeError(
            f"cannot list directory {directory}: {exc.strerror or exc}"
        ) from exc

    merged: dict[str, set[str]] = {}
    for path in files:
        for index, record in enumerate(load_file(path), start=1):
            item_id, tags = parse_record(record, path, index)
            merged.setdefault(item_id, set()).update(tags)

    return {
        item_id: sorted(merged[item_id])
        for item_id in sorted(merged)
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Merge tags by ID from JSON files in a directory."
    )
    parser.add_argument("data_dir", type=Path, help="directory containing JSON files")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = merge_directory(args.data_dir)
    except TagMergeError as exc:
        print(f"tagmerge: error: {exc}", file=sys.stderr)
        return 2

    json.dump(result, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
