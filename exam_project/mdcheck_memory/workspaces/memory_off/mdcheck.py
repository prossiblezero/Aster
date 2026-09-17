"""Check local path targets in Markdown files."""

from __future__ import annotations

import argparse
import json
import os
import re
import string
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Sequence
from urllib.parse import unquote

DEFAULT_ROOT = Path("manual")
_FENCE_RE = re.compile(r"^[ \t]*(`{3,}|~{3,})[ \t]*$")
_LINK_START_RE = re.compile(r"!?\[[^\]\n]*\]\(")
_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_ATX_HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.*)$")
_CLOSING_HEADING_MARKERS_RE = re.compile(r"[ \t]+#+[ \t]*$")
_ASCII_SLUG_PUNCTUATION = str.maketrans(
    "", "", string.punctuation.replace("-", "").replace("_", "")
)


class MdcheckError(Exception):
    """An invalid input or filesystem operation."""


@dataclass(frozen=True, order=True)
class BrokenLink:
    source: str
    line: int
    target: str
    reason: str = "missing_target"


@dataclass(frozen=True)
class CheckResult:
    checked: int
    broken: tuple[BrokenLink, ...]


def _markdown_files(root: Path) -> tuple[Path, Path, list[Path]]:
    """Return the display base, scan root, and deterministically ordered files."""
    if not root.exists():
        raise MdcheckError(f"input does not exist: {root}")
    if root.is_symlink():
        raise MdcheckError(f"input must not be a symbolic link: {root}")
    if root.is_file():
        if root.suffix.lower() != ".md":
            raise MdcheckError(f"input file is not Markdown: {root}")
        return root.parent, root, [root]
    if not root.is_dir():
        raise MdcheckError(f"input is neither a file nor a directory: {root}")

    files: list[Path] = []

    def scan_error(error: OSError) -> None:
        raise MdcheckError(f"cannot scan {root}: {error}") from error

    try:
        for directory, dirnames, filenames in os.walk(
            root, followlinks=False, onerror=scan_error
        ):
            current = Path(directory)
            dirnames[:] = sorted(
                name for name in dirnames if not (current / name).is_symlink()
            )
            for name in sorted(filenames):
                candidate = current / name
                if candidate.suffix.lower() == ".md" and not candidate.is_symlink():
                    files.append(candidate)
    except OSError as error:
        raise MdcheckError(f"cannot scan {root}: {error}") from error

    files.sort(key=lambda path: path.relative_to(root).as_posix())
    return root, root, files


def _destinations(line: str) -> Iterable[str]:
    """Yield inline link destinations from one non-fenced Markdown line."""
    position = 0
    while match := _LINK_START_RE.search(line, position):
        cursor = match.end()
        depth = 1
        escaped = False
        while cursor < len(line):
            character = line[cursor]
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == "(":
                depth += 1
            elif character == ")":
                depth -= 1
                if depth == 0:
                    break
            cursor += 1
        if depth != 0:
            return

        contents = line[match.end() : cursor].lstrip()
        if contents.startswith("<"):
            closing = contents.find(">", 1)
            if closing != -1:
                yield contents[1:closing]
        elif contents:
            destination: list[str] = []
            escaped = False
            for character in contents:
                if not escaped and character.isspace():
                    break
                destination.append(character)
                if character == "\\" and not escaped:
                    escaped = True
                else:
                    escaped = False
            yield "".join(destination)
        else:
            yield ""
        position = cursor + 1


def _local_target(target: str) -> tuple[str, str | None] | None:
    """Return a decoded local path and fragment, or None when skipped."""
    if not target or target.startswith("//") or _SCHEME_RE.match(target):
        return None
    if "#" in target:
        path_part, fragment = target.split("#", 1)
        return unquote(path_part), unquote(fragment)
    return unquote(target), None


def _unfenced_lines(lines: Iterable[str]) -> Iterable[tuple[int, str]]:
    """Yield numbered lines outside supported fenced code blocks."""
    fence_character: str | None = None
    fence_length = 0
    for line_number, line in enumerate(lines, start=1):
        fence = _FENCE_RE.fullmatch(line)
        if fence:
            marker = fence.group(1)
            if fence_character is None:
                fence_character = marker[0]
                fence_length = len(marker)
            elif marker[0] == fence_character and len(marker) >= fence_length:
                fence_character = None
                fence_length = 0
            continue
        if fence_character is None:
            yield line_number, line


def _heading_slug(heading: str) -> str:
    heading = _CLOSING_HEADING_MARKERS_RE.sub("", heading).strip().lower()
    heading = heading.translate(_ASCII_SLUG_PUNCTUATION)
    return re.sub(r"\s+", "-", heading)


def _anchors(lines: Iterable[str]) -> set[str]:
    """Build ATX heading anchors, including deterministic duplicate suffixes."""
    anchors: set[str] = set()
    slug_counts: dict[str, int] = {}
    for _line_number, line in _unfenced_lines(lines):
        heading = _ATX_HEADING_RE.match(line)
        if heading is None:
            continue
        slug = _heading_slug(heading.group(2))
        duplicate_number = slug_counts.get(slug, 0)
        slug_counts[slug] = duplicate_number + 1
        anchors.add(slug if duplicate_number == 0 else f"{slug}-{duplicate_number}")
    return anchors


def _read_lines(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        raise MdcheckError(f"cannot read {path}: {error}") from error


def check(root: str | os.PathLike[str] = DEFAULT_ROOT) -> CheckResult:
    """Check local Markdown links below root without modifying any files."""
    requested_root = Path(root)
    display_base, _scan_root, files = _markdown_files(requested_root)
    checked = 0
    broken: list[BrokenLink] = []
    anchor_cache: dict[Path, set[str]] = {}

    for source_path in files:
        lines = _read_lines(source_path)
        source = source_path.relative_to(display_base).as_posix()

        for line_number, line in _unfenced_lines(lines):
            for target in _destinations(line):
                local_target = _local_target(target)
                if local_target is None:
                    continue
                local_path, fragment = local_target
                checked += 1
                candidate = source_path if not local_path else Path(local_path)
                if local_path and not candidate.is_absolute():
                    candidate = source_path.parent / candidate
                try:
                    exists = candidate.exists()
                except OSError as error:
                    raise MdcheckError(f"cannot inspect {candidate}: {error}") from error
                if not exists:
                    broken.append(
                        BrokenLink(
                            source=source,
                            line=line_number,
                            target=target,
                        )
                    )
                    continue

                if (
                    fragment is not None
                    and fragment
                    and candidate.suffix.lower() == ".md"
                    and candidate.is_file()
                ):
                    cache_key = candidate.resolve()
                    if cache_key not in anchor_cache:
                        target_lines = lines if candidate == source_path else _read_lines(candidate)
                        anchor_cache[cache_key] = _anchors(target_lines)
                    if fragment not in anchor_cache[cache_key]:
                        broken.append(
                            BrokenLink(
                                source=source,
                                line=line_number,
                                target=target,
                                reason="missing_anchor",
                            )
                        )

    broken.sort()
    return CheckResult(checked=checked, broken=tuple(broken))


def _print_text(result: CheckResult) -> None:
    for item in result.broken:
        print(f"{item.source}:{item.line}: {item.target}: {item.reason}")
    count = len(result.broken)
    print(f"Checked {result.checked} local link(s); found {count} broken link(s).")


def _print_json(result: CheckResult) -> None:
    payload = {
        "checked": result.checked,
        "broken": [asdict(item) for item in result.broken],
    }
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mdcheck", description="Check local path targets in Markdown files."
    )
    parser.add_argument("root", nargs="?", default=str(DEFAULT_ROOT), metavar="ROOT")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = check(args.root)
    except MdcheckError as error:
        print(f"mdcheck: {error}", file=sys.stderr)
        return 2

    if args.format == "json":
        _print_json(result)
    else:
        _print_text(result)
    return 1 if result.broken else 0


if __name__ == "__main__":
    raise SystemExit(main())
