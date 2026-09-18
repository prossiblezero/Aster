"""Bounded skill discovery and explicit text reads; skills never change permissions."""

from __future__ import annotations

import json
import os
import re
import stat
import textwrap
from contextlib import contextmanager
from pathlib import Path

from .workspace import CONTROL, IGNORED, sensitive

SKILL_NAME = re.compile(r"[A-Za-z0-9_.-]{1,80}")
MAX_SKILL_BYTES = 20 * 1024
MAX_METADATA_BYTES = 4 * 1024
MAX_CATALOG_ENTRIES = 256
MAX_RESOURCES = 64
MAX_RESOURCE_ENTRIES = 128
MAX_RESOURCE_DEPTH = 3


def _valid_name(name: str) -> bool:
    return bool(
        SKILL_NAME.fullmatch(name)
        and name not in {".", ".."} | CONTROL
        and not sensitive(Path(name))
    )


def _metadata(lines: list[str], name: str) -> dict[str, str]:
    """A small, documented frontmatter subset; no YAML tags, aliases or objects."""
    values = {}
    index = 0
    while index < len(lines):
        line = lines[index]
        index += 1
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = re.fullmatch(r"([A-Za-z][A-Za-z0-9_-]*):\s*(.*)", line)
        if not match:
            raise ValueError("invalid skill metadata: expected an unindented key: value")
        key, value = match.groups()
        block = []
        while index < len(lines) and (not lines[index].strip() or lines[index].startswith(" ")):
            block.append(lines[index])
            index += 1
        if key not in {"name", "description"}:
            continue  # Unknown metadata cannot grant permissions or execute anything.
        if key in values:
            raise ValueError(f"invalid skill metadata: duplicate {key}")
        if value in {"|", "|-", "|+", ">", ">-", ">+"}:
            value_text = textwrap.dedent("\n".join(block)).strip()
            value = re.sub(r"(?<=\S)\n(?=\S)", " ", value_text) if value[0] == ">" else value_text
        else:
            if any(item.strip() for item in block):
                raise ValueError(f"invalid skill metadata: unsupported multiline {key}")
            if value.startswith('"'):
                try:
                    value = json.loads(value)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"invalid skill metadata: quoted {key}") from exc
            elif value.startswith("'"):
                if not re.fullmatch(r"'(?:[^']|'')*'", value):
                    raise ValueError(f"invalid skill metadata: quoted {key}")
                value = value[1:-1].replace("''", "'")
            elif (
                not value
                or value[0] in "[{&*!"
                or value.casefold() in {"true", "false", "null", "~"}
                or re.fullmatch(r"[-+]?\d+(?:\.\d+)?", value)
            ):
                raise ValueError(f"invalid skill metadata: {key} must be a string")
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"invalid skill metadata: {key} must be a nonempty string")
        values[key] = value.strip()
    if values.get("name", name) != name:
        raise ValueError("invalid skill metadata: name must match its file or package directory")
    if len(values.get("description", "")) > 1000:
        raise ValueError("skill description exceeds 1000 characters")
    return values


class Skills:
    def __init__(self, workspace: Path):
        self.workspace = workspace.resolve()
        self.dirs = [self.workspace / ".aster" / "skills", self.workspace / ".skills"]

    def _safe_path(self, path: Path) -> bool:
        if not path.is_relative_to(self.workspace):
            return False
        cursor = self.workspace
        for part in path.relative_to(self.workspace).parts:
            cursor /= part
            if part == ".." or cursor.is_symlink():
                return False
        return path.resolve().is_relative_to(self.workspace)

    @contextmanager
    def _directory(self, path: Path):
        """Anchor reads/listings so swapped symlink parents cannot redirect them."""
        if not self._safe_path(path):
            raise FileNotFoundError("skill directory unavailable")
        directory = os.open(self.workspace, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for part in path.relative_to(self.workspace).parts:
                child = os.open(
                    part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory
                )
                os.close(directory)
                directory = child
            yield directory
        finally:
            os.close(directory)

    @contextmanager
    def _open(self, path: Path):
        if not self._safe_path(path) or not path.is_file():
            raise FileNotFoundError("skill file unavailable")
        if path.stat().st_size > MAX_SKILL_BYTES:
            raise ValueError("skill exceeds 20 KiB")
        descriptor = None
        with self._directory(path.parent) as directory:
            descriptor = os.open(
                path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory
            )
            try:
                info = os.fstat(descriptor)
                if not stat.S_ISREG(info.st_mode):
                    raise ValueError("skill must be a regular file")
                if info.st_size > MAX_SKILL_BYTES:
                    raise ValueError("skill exceeds 20 KiB")
                with os.fdopen(descriptor, "rb") as stream:
                    descriptor = None
                    yield stream
            finally:
                if descriptor is not None:
                    os.close(descriptor)

    def _header(self, stream, name: str) -> dict[str, str]:
        first = stream.readline(5)
        if first not in {b"---\n", b"---\r\n"}:
            stream.seek(0)
            return {}
        used = len(first)
        lines = []
        while True:
            line = stream.readline(MAX_METADATA_BYTES - used + 1)
            used += len(line)
            if used > MAX_METADATA_BYTES:
                raise ValueError("skill metadata exceeds 4 KiB")
            if not line:
                raise ValueError("invalid skill metadata: missing closing ---")
            if line.rstrip(b"\r\n") == b"---":
                return _metadata(lines, name)
            try:
                lines.append(line.decode("utf-8").rstrip("\r\n"))
            except UnicodeDecodeError as exc:
                raise ValueError("skill metadata must be UTF-8") from exc

    def _find(self, name: str) -> Path:
        if not _valid_name(name):
            raise ValueError("invalid skill name")
        for root in self.dirs:
            for path in (root / name / "SKILL.md", root / f"{name}.md"):
                if self._safe_path(path) and path.is_file():
                    return path
        raise FileNotFoundError(name)

    def _names(self) -> list[str]:
        names = set()
        for root in self.dirs:
            if not self._safe_path(root) or not root.is_dir():
                continue
            with self._directory(root) as directory, os.scandir(directory) as entries:
                for count, entry in enumerate(entries, 1):
                    if count > MAX_CATALOG_ENTRIES:
                        raise ValueError("skill directory exceeds 256 entries")
                    if entry.is_symlink():
                        continue
                    name = (
                        entry.name if entry.is_dir(follow_symlinks=False) else Path(entry.name).stem
                    )
                    if _valid_name(name) and (
                        entry.is_dir(follow_symlinks=False) or entry.name.endswith(".md")
                    ):
                        names.add(name)
        return sorted(names)

    def _resources(self, base: Path) -> tuple[list[str], bool]:
        resources, pending = [], [(base, 0)]
        visited, truncated = 0, False
        while pending:
            directory, depth = pending.pop()
            if not self._safe_path(directory):
                continue
            with self._directory(directory) as descriptor, os.scandir(descriptor) as entries:
                for entry in entries:
                    visited += 1
                    if visited > MAX_RESOURCE_ENTRIES or len(resources) >= MAX_RESOURCES:
                        return sorted(resources), True
                    path = directory / entry.name
                    relative = path.relative_to(base)
                    if (
                        entry.is_symlink()
                        or sensitive(relative)
                        or any(p in CONTROL | IGNORED for p in relative.parts)
                    ):
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        if depth < MAX_RESOURCE_DEPTH:
                            pending.append((path, depth + 1))
                        else:
                            truncated = True
                    elif entry.is_file(follow_symlinks=False) and relative != Path("SKILL.md"):
                        if entry.stat(follow_symlinks=False).st_size <= MAX_SKILL_BYTES:
                            resources.append(relative.as_posix())
        return sorted(resources), truncated

    def catalog(self) -> list[dict]:
        """Read headers and bounded filenames only; malformed entries remain diagnosable."""
        records = []
        for name in self._names():
            try:
                path = self._find(name)
            except FileNotFoundError:
                continue
            package = path.name == "SKILL.md"
            record = {
                "name": name,
                "description": "",
                "path": path.relative_to(self.workspace).as_posix(),
                "base_dir": path.parent.relative_to(self.workspace).as_posix() if package else None,
                "resources": [],
                "resources_truncated": False,
            }
            try:
                with self._open(path) as stream:
                    metadata = self._header(stream, name)
                record["description"] = metadata.get("description", "")
                if package:
                    record["resources"], record["resources_truncated"] = self._resources(
                        path.parent
                    )
            except (OSError, ValueError) as exc:
                record["error"] = str(exc)
            records.append(record)
        return records

    def list(self) -> list[str]:
        return [item["name"] for item in self.catalog() if "error" not in item]

    def load(self, name: str, resource: str = "") -> str:
        path = self._find(name)
        with self._open(path) as stream:
            self._header(stream, name)
            data = stream.read(MAX_SKILL_BYTES + 1) if not resource else b""
            if stream.tell() > MAX_SKILL_BYTES:
                raise ValueError("skill exceeds 20 KiB")
        if resource:
            if path.name != "SKILL.md":
                raise ValueError("legacy skills have no package resources")
            relative = Path(resource)
            if (
                relative.is_absolute()
                or "\\" in resource
                or "\x00" in resource
                or ".." in relative.parts
                or ":" in resource
                or sensitive(relative)
                or any(p in CONTROL | IGNORED for p in relative.parts)
            ):
                raise ValueError("invalid skill resource path")
            path = path.parent / relative
            with self._open(path) as stream:
                data = stream.read(MAX_SKILL_BYTES + 1)
        if len(data) > MAX_SKILL_BYTES:
            raise ValueError("skill exceeds 20 KiB")
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("skill content must be UTF-8") from exc
