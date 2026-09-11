"""Workspace boundary, freshness and snapshots shared by tools and recovery."""

from __future__ import annotations

import difflib
import hashlib
import json
import os
from pathlib import Path

CONTROL = {".aster", ".git", ".agents", ".codex", ".claude"}
IGNORED = {"__pycache__", ".pytest_cache", ".ruff_cache", ".venv", "node_modules", ".tmp"}


class BoundaryError(ValueError):
    pass


def sensitive(path: Path) -> bool:
    return any(p.startswith(".env") or p in {".ssh", "id_rsa", "id_ed25519"}
               or p.endswith((".pem", ".key")) for p in path.parts)


def digest(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode()
    return hashlib.sha256(data).hexdigest()


class Workspace:
    def __init__(self, root: Path):
        root.mkdir(parents=True, exist_ok=True)
        self.root = root.resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError("workspace must be a directory")

    def path(self, raw: str, *, write: bool = False) -> Path:
        candidate = Path(raw)
        candidate = candidate if candidate.is_absolute() else self.root / candidate
        try:
            relative = candidate.relative_to(self.root)
        except ValueError as exc:
            raise BoundaryError("path_escape") from exc
        cursor = self.root
        for part in relative.parts:
            if part == "..":
                raise BoundaryError("path_escape")
            cursor /= part
            if cursor.is_symlink():
                raise BoundaryError("symlink_path")
        resolved = candidate.resolve()
        if not resolved.is_relative_to(self.root):
            raise BoundaryError("path_escape")
        if any(p in CONTROL for p in relative.parts):
            raise BoundaryError("protected_metadata")
        if sensitive(relative):
            raise BoundaryError("sensitive_path")
        if write and resolved == self.root:
            raise BoundaryError("cannot_write_workspace_root")
        return resolved

    def files(self, raw: str = ".") -> list[Path]:
        start = self.path(raw)
        if start.is_file():
            return [start]
        result = []
        for directory, dirs, files in os.walk(start, followlinks=False):
            dirs[:] = sorted(d for d in dirs if d not in CONTROL | IGNORED
                             and not sensitive(Path(d)) and not (Path(directory) / d).is_symlink())
            for name in sorted(files):
                path = Path(directory) / name
                if path.is_symlink() or sensitive(Path(name)) or name.endswith(".pyc"):
                    continue
                result.append(path)
                if len(result) > 10000:
                    raise BoundaryError("workspace exceeds 10000 files; narrow --cwd")
        return result

    def snapshot(self) -> dict[str, str]:
        result = {}
        for path in self.files():
            h = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    h.update(chunk)
            result[str(path.relative_to(self.root))] = h.hexdigest()
        return result

    def fingerprint(self) -> str:
        return digest(json.dumps(self.snapshot(), sort_keys=True))

    def read(self, raw: str) -> str:
        path = self.path(raw)
        if path.stat().st_size > 2 * 1024 * 1024:
            raise ValueError("file exceeds 2 MiB text limit")
        return path.read_text(encoding="utf-8")

    @staticmethod
    def changes(before: dict[str, str], after: dict[str, str]) -> list[str]:
        return sorted(k for k in before.keys() | after.keys() if before.get(k) != after.get(k))

    def diff(self, originals: dict[str, str]) -> str:
        current = {str(p.relative_to(self.root)): p for p in self.files()}
        chunks = []
        for name in sorted(originals.keys() | current.keys()):
            try:
                after = current[name].read_text() if name in current else ""
            except (UnicodeError, OSError):
                continue
            chunks.extend(difflib.unified_diff(originals.get(name, "").splitlines(True),
                                              after.splitlines(True), f"a/{name}", f"b/{name}"))
        return "".join(chunks)
