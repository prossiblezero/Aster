"""Workspace boundary, freshness and snapshots shared by tools and recovery."""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import stat
import subprocess
import tempfile
from contextlib import nullcontext
from pathlib import Path

CONTROL = {".aster", ".git", ".agents", ".codex", ".claude"}
IGNORED = {"__pycache__", ".pytest_cache", ".ruff_cache", ".venv", "node_modules", ".tmp"}


class BoundaryError(ValueError):
    pass


def sensitive(path: Path) -> bool:
    return any(
        p.startswith(".env")
        or p in {".ssh", "id_rsa", "id_ed25519"}
        or p.endswith((".pem", ".key"))
        for p in path.parts
    )


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
        self._git_repository = False
        result = self._git("rev-parse", "--is-inside-work-tree")
        self._git_repository = bool(result and result.stdout.strip() == b"true")
        # Explicitly selecting an ignored directory makes it the ignore boundary.
        # Its own rules still apply; the parent repository and index stay untouched.
        self._git_root_ignored = bool(
            self._git_repository
            and self._git("check-ignore", "--no-index", "-q", "--", ".").returncode == 0
        )

    def _git(self, *arguments: str, scoped: bool = False) -> subprocess.CompletedProcess | None:
        try:
            with tempfile.TemporaryDirectory(prefix="aster-ignore-") if scoped else nullcontext() as directory:
                # An inherited GIT_DIR/INDEX_FILE must not redirect workspace checks.
                env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
                options = []
                if scoped:
                    options = ["--work-tree", str(self.root)]
                    # No index: parent-relative paths must not exempt local names.
                    env["GIT_INDEX_FILE"] = str(Path(directory) / "index")
                result = subprocess.run(
                    ["git", "--no-optional-locks", "-c", "core.fsmonitor=false", "-C", str(self.root), *options, *arguments],
                    capture_output=True,
                    timeout=10,
                    env=env,
                )
        except FileNotFoundError:
            if not self._git_repository:
                return None
            raise BoundaryError("git_ignore_unavailable") from None
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise BoundaryError("git_ignore_unavailable") from exc
        if self._git_repository and result.returncode not in (0, 1):
            raise BoundaryError("git_ignore_unavailable")
        return result

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
            try:
                kind = cursor.lstat().st_mode
            except (FileNotFoundError, NotADirectoryError):
                continue
            if stat.S_ISLNK(kind):
                raise BoundaryError("symlink_path")
            if not (stat.S_ISREG(kind) or stat.S_ISDIR(kind)):
                raise BoundaryError("special_file")
        resolved = candidate.resolve()
        if not resolved.is_relative_to(self.root):
            raise BoundaryError("path_escape")
        if any(p in CONTROL for p in relative.parts):
            raise BoundaryError("protected_metadata")
        if sensitive(relative):
            raise BoundaryError("sensitive_path")
        if write and resolved == self.root:
            raise BoundaryError("cannot_write_workspace_root")
        if write and self._git_repository:
            if self._git_root_ignored:
                tracked = self._git("ls-files", "--cached", "-z").stdout.split(b"\0")
                if os.fsencode(relative) in tracked:
                    return resolved
            result = self._git("check-ignore", "-q", "--", str(relative), scoped=self._git_root_ignored)
            if result.returncode == 0:
                raise BoundaryError("git_ignored_path")
        return resolved

    def files(self, raw: str = ".") -> list[Path]:
        start = self.path(raw)
        if start.is_file():
            return [start]
        ignored = set()
        tracked = set()
        tracked_directories = set()
        if self._git_root_ignored:
            result = self._git("ls-files", "--cached", "-z")
            tracked = {self.root / os.fsdecode(p) for p in result.stdout.split(b"\0") if p}
            tracked_directories = {parent for path in tracked for parent in path.parents}
        if self._git_repository:
            result = self._git(
                "ls-files", "--others", "--ignored", "--exclude-standard", "--directory", "-z",
                scoped=self._git_root_ignored,
            )
            ignored = {self.root / os.fsdecode(p) for p in result.stdout.split(b"\0") if p}

        def excluded(path):
            return (
                path not in tracked
                and path not in tracked_directories
                and any(parent in ignored for parent in (path, *path.parents))
            )

        if excluded(start):
            return []
        result = []
        for directory, dirs, files in os.walk(start, followlinks=False):
            dirs[:] = sorted(
                d
                for d in dirs
                if d not in CONTROL | IGNORED
                and not sensitive(Path(d))
                and not (Path(directory) / d).is_symlink()
                and not excluded(Path(directory) / d)
            )
            for name in sorted(files):
                path = Path(directory) / name
                if (
                    name in CONTROL
                    or path.is_symlink()
                    or sensitive(Path(name))
                    or name.endswith(".pyc")
                    or excluded(path)
                ):
                    continue
                if not path.is_file():
                    raise BoundaryError("special_file")
                result.append(path)
                if len(result) > 10000:
                    raise BoundaryError("workspace exceeds 10000 files; narrow --cwd")
        return result

    def snapshot(self) -> dict[str, str]:
        """File identity includes permission bits; read evidence separately hashes text."""
        result = {}
        for path in self.files():
            h = hashlib.sha256()
            h.update(f"{stat.S_IMODE(path.stat().st_mode):04o}\0".encode())
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    h.update(chunk)
            result[str(path.relative_to(self.root))] = h.hexdigest()
        return result

    def mode_snapshot(self) -> dict[str, int]:
        return {
            str(path.relative_to(self.root)): stat.S_IMODE(path.stat().st_mode)
            for path in self.files()
        }

    def fingerprint(self) -> str:
        return digest(json.dumps(self.snapshot(), sort_keys=True))

    def text_snapshot(self) -> dict[str, str]:
        """Text baseline for a human-readable diff; hashes remain drift evidence."""
        result = {}
        for path in self.files():
            try:
                name = str(path.relative_to(self.root))
                result[name] = self.read(name)
            except (UnicodeError, ValueError):
                continue
        return result

    def read(self, raw: str) -> str:
        path = self.path(raw)
        if path.stat().st_size > 2 * 1024 * 1024:
            raise ValueError("file exceeds 2 MiB text limit")
        return path.read_text(encoding="utf-8")

    @staticmethod
    def changes(before: dict[str, str], after: dict[str, str]) -> list[str]:
        return sorted(k for k in before.keys() | after.keys() if before.get(k) != after.get(k))

    def diff(self, originals: dict[str, str], modes: dict[str, int] | None = None) -> str:
        current = {str(p.relative_to(self.root)): p for p in self.files()}
        chunks = []
        for name in sorted(originals.keys() | current.keys()):
            if modes is not None and name in modes and name in current:
                mode = stat.S_IMODE(current[name].stat().st_mode)
                if mode != modes[name]:
                    chunks.extend(
                        [
                            f"diff --git a/{name} b/{name}\n",
                            f"old mode {stat.S_IFREG | modes[name]:06o}\n",
                            f"new mode {stat.S_IFREG | mode:06o}\n",
                        ]
                    )
            try:
                after = current[name].read_text() if name in current else ""
            except (UnicodeError, OSError):
                continue
            chunks.extend(
                difflib.unified_diff(
                    originals.get(name, "").splitlines(True),
                    after.splitlines(True),
                    f"a/{name}",
                    f"b/{name}",
                )
            )
        return "".join(chunks)
