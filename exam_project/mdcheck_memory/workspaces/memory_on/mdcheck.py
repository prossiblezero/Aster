"""Check local targets in Markdown inline links."""

from __future__ import annotations

import argparse
import json
import os
import re
import string
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterator, Sequence
from urllib.parse import unquote

DEFAULT_ROOT = Path("docs")
_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_FENCE_RE = re.compile(r"^[ \t]*(`{3,}|~{3,})[^\r\n]*$")
_ESCAPABLE = frozenset(string.punctuation)


@dataclass(frozen=True, order=True)
class BrokenLink:
    source: str
    line: int
    target: str
    reason: str = "missing_target"


def _raise_walk_error(error: OSError) -> None:
    raise error


def _markdown_files(root: Path) -> tuple[Path, list[Path]]:
    """Return the source base and sorted Markdown files below root."""
    if root.is_symlink():
        return (root.parent if not root.is_dir() else root), []
    if root.is_file():
        return root.parent, [root] if root.suffix.lower() == ".md" else []

    files: list[Path] = []
    for current, dirnames, filenames in os.walk(
        root, followlinks=False, onerror=_raise_walk_error
    ):
        current_path = Path(current)
        dirnames[:] = sorted(
            name for name in dirnames if not (current_path / name).is_symlink()
        )
        for name in sorted(filenames):
            path = current_path / name
            if path.suffix.lower() == ".md" and not path.is_symlink():
                files.append(path)
    files.sort(key=lambda path: path.relative_to(root).as_posix())
    return root, files


def _closing_bracket(text: str, start: int) -> int | None:
    depth = 1
    index = start
    while index < len(text):
        char = text[index]
        if char == "\\" and index + 1 < len(text):
            index += 2
            continue
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return None


def _closing_parenthesis_after_title(text: str, index: int) -> int | None:
    while index < len(text) and text[index] in " \t":
        index += 1
    if index < len(text) and text[index] == ")":
        return index
    if index >= len(text) or text[index] not in "\"'":
        return None

    quote = text[index]
    index += 1
    while index < len(text):
        if text[index] == "\\" and index + 1 < len(text):
            index += 2
            continue
        if text[index] == quote:
            index += 1
            while index < len(text) and text[index] in " \t":
                index += 1
            return index if index < len(text) and text[index] == ")" else None
        index += 1
    return None


def _destination(text: str, opening_parenthesis: int) -> tuple[str, int] | None:
    index = opening_parenthesis + 1
    while index < len(text) and text[index] in " \t":
        index += 1

    if index < len(text) and text[index] == "<":
        start = index + 1
        index = start
        while index < len(text):
            if text[index] == "\\" and index + 1 < len(text):
                index += 2
                continue
            if text[index] == ">":
                closing = _closing_parenthesis_after_title(text, index + 1)
                return (text[start:index], closing) if closing is not None else None
            if text[index] == "<":
                return None
            index += 1
        return None

    start = index
    depth = 0
    while index < len(text):
        char = text[index]
        if char == "\\" and index + 1 < len(text):
            index += 2
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            if depth == 0:
                return text[start:index], index
            depth -= 1
        elif char in " \t" and depth == 0:
            closing = _closing_parenthesis_after_title(text, index)
            return (text[start:index], closing) if closing is not None else None
        index += 1
    return None


def _inline_targets(line: str) -> Iterator[str]:
    index = 0
    while index < len(line):
        if line[index] != "[":
            index += 1
            continue
        label_end = _closing_bracket(line, index + 1)
        if label_end is None:
            break
        if label_end + 1 < len(line) and line[label_end + 1] == "(":
            parsed = _destination(line, label_end + 1)
            if parsed is not None:
                target, closing = parsed
                yield target
                index = closing + 1
                continue
        index = label_end + 1


def _targets_outside_fences(text: str) -> Iterator[tuple[int, str]]:
    fence_char: str | None = None
    fence_length = 0
    for line_number, line in enumerate(text.splitlines(), start=1):
        if fence_char is not None:
            closing = re.fullmatch(
                rf"[ \t]*{re.escape(fence_char)}{{{fence_length},}}[ \t]*", line
            )
            if closing:
                fence_char = None
                fence_length = 0
            continue

        opening = _FENCE_RE.fullmatch(line)
        if opening:
            fence = opening.group(1)
            fence_char = fence[0]
            fence_length = len(fence)
            continue
        for target in _inline_targets(line):
            yield line_number, target


def _markdown_unescape(value: str) -> str:
    result: list[str] = []
    index = 0
    while index < len(value):
        if (
            value[index] == "\\"
            and index + 1 < len(value)
            and value[index + 1] in _ESCAPABLE
        ):
            index += 1
        result.append(value[index])
        index += 1
    return "".join(result)


def _local_path(target: str) -> str | None:
    unescaped = _markdown_unescape(target)
    if not unescaped or unescaped.startswith("#"):
        return None if unescaped.startswith("#") else ""
    if unescaped.startswith("//") or _SCHEME_RE.match(unescaped):
        return None
    path = unescaped.split("#", 1)[0].split("?", 1)[0]
    return unquote(path, encoding="utf-8", errors="replace")


def check(root: Path) -> tuple[int, list[BrokenLink]]:
    """Check root and return the number checked and all missing targets."""
    source_base, files = _markdown_files(root)
    checked = 0
    broken: list[BrokenLink] = []

    for source in files:
        text = source.read_text(encoding="utf-8")
        source_name = source.relative_to(source_base).as_posix()
        for line_number, target in _targets_outside_fences(text):
            local_path = _local_path(target)
            if local_path is None:
                continue
            checked += 1
            candidate = source if local_path == "" else source.parent / local_path
            if not candidate.exists():
                broken.append(BrokenLink(source_name, line_number, target))

    broken.sort()
    return checked, broken


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mdcheck", description="Check local targets in Markdown inline links."
    )
    parser.add_argument("root", nargs="?", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--format", choices=("text", "json"), default="text")
    return parser


def _write_result(output_format: str, checked: int, broken: list[BrokenLink]) -> None:
    if output_format == "json":
        print(
            json.dumps(
                {"checked": checked, "broken": [asdict(item) for item in broken]},
                ensure_ascii=False,
            )
        )
        return

    print(f"Checked {checked} local link(s); found {len(broken)} broken.")
    for item in broken:
        print(f"{item.source}:{item.line}: {item.target} ({item.reason})")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root: Path = args.root
    if not root.exists():
        print(f"mdcheck: root does not exist: {root}", file=sys.stderr)
        return 2
    if not root.is_file() and not root.is_dir():
        print(f"mdcheck: root is not a file or directory: {root}", file=sys.stderr)
        return 2

    try:
        checked, broken = check(root)
    except (OSError, UnicodeError) as error:
        print(f"mdcheck: {error}", file=sys.stderr)
        return 2

    _write_result(args.format, checked, broken)
    return 1 if broken else 0


if __name__ == "__main__":
    raise SystemExit(main())
