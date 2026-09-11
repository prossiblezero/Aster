"""Directory-only lazy skills; skill text is untrusted context."""

from __future__ import annotations

import re
from pathlib import Path


class Skills:
    def __init__(self, workspace: Path):
        self.dirs = [workspace / ".aster" / "skills", workspace / ".skills"]

    def list(self) -> list[str]:
        return sorted({p.stem for root in self.dirs if root.is_dir() for p in root.glob("*.md")})

    def load(self, name: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", name):
            raise ValueError("invalid skill name")
        for root in self.dirs:
            path = root / f"{name}.md"
            if path.exists() and path.is_file():
                text = path.read_text(encoding="utf-8")
                if len(text) > 20_000:
                    raise ValueError("skill exceeds 20 KiB")
                return text
        raise FileNotFoundError(name)
