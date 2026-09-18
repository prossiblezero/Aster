#!/usr/bin/env python3
"""Count tagged notes in a directory tree."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import NoReturn

DEFAULT_NOTE_DIRECTORY = Path("inbox_notes")


class NoteTallyError(Exception):
    """An expected input or filesystem error."""


class ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        self.exit(2, f"{self.prog}: error: {message}\n")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = ArgumentParser(
        description="Count labels in UTF-8 .note files recursively."
    )
    parser.add_argument(
        "--total",
        action="store_true",
        help="output only the total number of notes across all labels",
    )
    parser.add_argument(
        "directory",
        nargs="?",
        type=Path,
        default=DEFAULT_NOTE_DIRECTORY,
        help="note directory (default: inbox_notes/)",
    )
    return parser.parse_args(argv)


def note_files(directory: Path) -> list[Path]:
    try:
        if not directory.exists():
            raise NoteTallyError(f"directory does not exist: {directory}")
        if not directory.is_dir():
            raise NoteTallyError(f"not a directory: {directory}")

        files: list[Path] = []

        def raise_walk_error(error: OSError) -> None:
            raise error

        for root, directory_names, file_names in os.walk(
            directory, onerror=raise_walk_error, followlinks=False
        ):
            directory_names.sort()
            for file_name in sorted(file_names):
                if file_name.endswith(".note"):
                    files.append(Path(root) / file_name)
        return files
    except NoteTallyError:
        raise
    except OSError as error:
        raise NoteTallyError(f"cannot read directory {directory}: {error}") from None


def tally_file(path: Path, counts: Counter[str]) -> None:
    try:
        with path.open("r", encoding="utf-8", errors="strict") as note_file:
            for line_number, raw_line in enumerate(note_file, start=1):
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                if ":" not in line:
                    raise NoteTallyError(
                        f"invalid note at {path}:{line_number}: missing ':'"
                    )
                label, content = (part.strip() for part in line.split(":", 1))
                if not label or not content:
                    raise NoteTallyError(
                        f"invalid note at {path}:{line_number}: "
                        "label and content must be non-empty"
                    )
                counts[label] += 1
    except NoteTallyError:
        raise
    except UnicodeError as error:
        raise NoteTallyError(f"invalid UTF-8 in {path}: {error}") from None
    except OSError as error:
        raise NoteTallyError(f"cannot read note file {path}: {error}") from None


def tally(directory: Path) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for path in note_files(directory):
        tally_file(path, counts)
    return dict(counts)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = tally(args.directory)
    except NoteTallyError as error:
        print(f"notetally.py: error: {error}", file=sys.stderr)
        return 2

    output = sum(result.values()) if args.total else result
    print(json.dumps(output, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
