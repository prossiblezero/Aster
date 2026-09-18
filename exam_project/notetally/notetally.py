#!/usr/bin/env python3
"""Recursively count labels in UTF-8 .note files."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Sequence

DEFAULT_NOTE_DIRECTORY = Path("inbox_notes")


class NoteTallyError(Exception):
    """An input error that should be reported without a traceback."""


def parse_note_line(line: str, source: Path, line_number: int) -> str | None:
    stripped = line.strip()
    if not stripped or line.lstrip().startswith("#"):
        return None

    label, separator, content = line.partition(":")
    label = label.strip()
    content = content.strip()
    if not separator or not label or not content:
        raise NoteTallyError(
            f"格式错误: {source}:{line_number}: 应为非空的‘标签: 内容’"
        )
    return label


def tally_notes(directory: Path) -> Counter[str]:
    if not directory.is_dir():
        raise NoteTallyError(f"不是可用目录: {directory}")

    counts: Counter[str] = Counter()

    def handle_walk_error(error: OSError) -> None:
        raise NoteTallyError(f"无法读取目录: {error.filename or directory}: {error.strerror}")

    try:
        for root, directories, filenames in os.walk(directory, onerror=handle_walk_error):
            directories.sort()
            for filename in sorted(filenames):
                if not filename.endswith(".note"):
                    continue
                path = Path(root) / filename
                try:
                    with path.open("r", encoding="utf-8") as note_file:
                        for line_number, line in enumerate(note_file, start=1):
                            label = parse_note_line(line, path, line_number)
                            if label is not None:
                                counts[label] += 1
                except UnicodeDecodeError as error:
                    raise NoteTallyError(f"无效 UTF-8: {path}: {error}") from error
                except OSError as error:
                    raise NoteTallyError(f"无法读取文件: {path}: {error}") from error
    except OSError as error:
        raise NoteTallyError(f"无法读取目录: {directory}: {error}") from error

    return counts


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="notetally.py",
        description="递归统计 .note 文件中的便签标签。",
    )
    parser.add_argument(
        "directory",
        nargs="?",
        default=DEFAULT_NOTE_DIRECTORY,
        type=Path,
        help="便签目录（默认: inbox_notes/）",
    )
    parser.add_argument(
        "--total",
        action="store_true",
        help="只输出所有标签的便签总数",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        counts = tally_notes(args.directory)
    except NoteTallyError as error:
        print(f"错误: {error}", file=sys.stderr)
        return 2

    output = sum(counts.values()) if args.total else counts
    print(json.dumps(output, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
