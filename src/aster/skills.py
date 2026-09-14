"""Directory-only lazy skills; skill text is untrusted context."""

from __future__ import annotations

import re
from pathlib import Path

SKILL_NAME = re.compile(r"[A-Za-z0-9_.-]{1,80}")
MAX_SKILL_BYTES = 20 * 1024


class Skills:
    def __init__(self, workspace: Path):
        self.workspace = workspace.resolve()
        self.dirs = [self.workspace / ".aster" / "skills", self.workspace / ".skills"]

    def _safe_path(self, path: Path) -> bool:
        """Skill metadata may be read, but no component may redirect outside its root."""
        if not path.is_relative_to(self.workspace):
            return False
        cursor = self.workspace
        for part in path.relative_to(self.workspace).parts:
            cursor /= part
            if cursor.is_symlink():
                return False
        return path.resolve().is_relative_to(self.workspace)

    def list(self) -> list[str]:
        return sorted(
            {
                p.stem
                for root in self.dirs
                if self._safe_path(root) and root.is_dir()
                for p in root.glob("*.md")
                if SKILL_NAME.fullmatch(p.stem) and self._safe_path(p) and p.is_file()
            }
        )

    def load(self, name: str) -> str:
        if not SKILL_NAME.fullmatch(name):
            raise ValueError("invalid skill name")
        for root in self.dirs:
            path = root / f"{name}.md"
            if self._safe_path(path) and path.is_file():
                if path.stat().st_size > MAX_SKILL_BYTES:
                    raise ValueError("skill exceeds 20 KiB")
                with path.open("rb") as stream:
                    data = stream.read(MAX_SKILL_BYTES + 1)
                if len(data) > MAX_SKILL_BYTES:
                    raise ValueError("skill exceeds 20 KiB")
                return data.decode("utf-8")
        raise FileNotFoundError(name)
