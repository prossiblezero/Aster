"""Atomic checkpoints and a redacted append-only event stream."""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .workspace import BoundaryError, digest


def atomic_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".write-")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


class Redactor:
    def __init__(self, secrets: list[str] = ()):
        self.secrets = sorted({x for x in secrets if len(x) >= 6}, key=len, reverse=True)

    def text(self, text: str) -> str:
        for secret in self.secrets:
            text = text.replace(secret, "[REDACTED]")
        return re.sub(r"(?i)\b(?:sk-[a-z0-9_-]{12,}|bearer\s+[a-z0-9._-]{12,})",
                      "[REDACTED]", text)

    def value(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, dict):
            return {k: "[REDACTED]" if re.search(r"(?i)(api_key|password|secret|authorization)", k)
                    else self.value(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self.value(v) for v in value]
        return value


class EvidenceStore:
    def __init__(self, workspace: Path, redactor: Redactor):
        self.root = workspace / ".aster"
        if self.root.is_symlink():
            raise BoundaryError("state directory cannot be a symlink")
        self.root.mkdir(mode=0o700, exist_ok=True)
        self.redactor = redactor
        self._lock = threading.RLock()
        self.listeners = []

    def path(self, *parts: str) -> Path:
        path = self.root.joinpath(*parts)
        if not path.resolve().is_relative_to(self.root.resolve()):
            raise BoundaryError("artifact_escape")
        cursor = path
        while cursor != self.root:
            if cursor.is_symlink():
                raise BoundaryError("artifact_symlink")
            cursor = cursor.parent
        return path

    def save(self, parts: tuple[str, ...], data: Any) -> None:
        with self._lock:
            atomic_json(self.path(*parts), self.redactor.value(data))

    def emit(self, run_id: str, event: str, **payload) -> dict:
        with self._lock:
            path = self.path("runs", run_id, "trace.jsonl")
            path.parent.mkdir(parents=True, exist_ok=True)
            rows = self.events(run_id)
            row = self.redactor.value({"seq": len(rows) + 1,
                "time": datetime.now(UTC).isoformat(), "run_id": run_id,
                "event": event, "previous": rows[-1]["hash"] if rows else "", **payload})
            row["hash"] = digest(json.dumps(row, sort_keys=True, ensure_ascii=False))
            with path.open("a") as stream:
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
                stream.flush()
            for listener in self.listeners:
                listener(row)
            return row

    def events(self, run_id: str) -> list[dict]:
        path = self.path("runs", run_id, "trace.jsonl")
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def artifact(self, run_id: str, call_id: str, content: str) -> str:
        name = digest(call_id)[:24] + ".txt"
        path = self.path("runs", run_id, "artifacts", name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.redactor.text(content))
        return str(path.relative_to(self.root))

    def list_runs(self) -> list[dict]:
        return [json.loads(p.read_text()) for p in sorted(self.root.glob("runs/*/report.json"))]
