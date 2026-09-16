"""Atomic checkpoints and a redacted append-only event stream."""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import threading
from copy import deepcopy
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
        return re.sub(r"(?i)\b(?:sk-[a-z0-9_-]{12,}|bearer\s+[a-z0-9._-]{12,})", "[REDACTED]", text)

    def value(self, value: Any, *, mask_fields: bool = True) -> Any:
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, dict):
            return {
                k: "[REDACTED]"
                if mask_fields and re.fullmatch(r"(?i)(api_key|password|secret|authorization)", k)
                else self.value(v, mask_fields=mask_fields)
                for k, v in value.items()
            }
        if isinstance(value, list):
            return [self.value(v, mask_fields=mask_fields) for v in value]
        return value


def _native_opaque_fields(history):
    """Exact protocol locations only; tool payloads and same-named metadata stay redacted."""
    for item in history:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "reasoning" and item.get("role") in {None, "assistant"}:
            if "encrypted_content" in item:
                yield item, "encrypted_content"
        if item.get("role") == "assistant" and isinstance(item.get("content"), list):
            for block in item["content"]:
                if not isinstance(block, dict):
                    continue
                key = {"thinking": "signature", "redacted_thinking": "data"}.get(block.get("type"))
                if key and key in block:
                    yield block, key


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

    def validate_native_history(self, history) -> None:
        """Reject damaged opaque state or credentials that cannot be persisted unchanged."""
        for block, key in _native_opaque_fields(history):
            value = block[key]
            if value is None:
                continue
            if not isinstance(value, str):
                raise ValueError("invalid_opaque_history")
            if "[REDACTED]" in value:
                raise ValueError("opaque_history_redacted: cannot safely resume damaged state")
            if any(secret in value for secret in self.redactor.secrets):
                raise ValueError("opaque_history_contains_credential: cannot safely persist state")
            if key == "signature":
                thinking = block.get("thinking", "")
                if (
                    not isinstance(thinking, str)
                    or "[REDACTED]" in thinking
                    or self.redactor.text(thinking) != thinking
                ):
                    raise ValueError(
                        "signed_thinking_requires_redaction: cannot safely persist state"
                    )

    def save(
        self, parts: tuple[str, ...], data: Any, *, preserve_native_history: bool = False
    ) -> None:
        with self._lock:
            # Checkpoint mappings use file paths as keys; keep their value types intact.
            redacted = self.redactor.value(data, mask_fields=False)
            if preserve_native_history:
                history = data.get("structured_history", [])
                self.validate_native_history(history)
                for (original, key), (saved, _) in zip(
                    _native_opaque_fields(history),
                    _native_opaque_fields(redacted.get("structured_history", [])),
                    strict=True,
                ):
                    saved[key] = original[key]
            atomic_json(self.path(*parts), redacted)

    def archive_observation(self, run_id: str, call_id: str, tool: str, content: str) -> dict:
        """Persist captured text before replacing it with a preview; never silently clip it."""
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", run_id):
            raise BoundaryError("invalid_observation_run")
        record = self.redactor.value(
            {"run_id": run_id, "call_id": call_id, "tool": tool, "content": content},
            mask_fields=False,
        )
        observation_id = digest(json.dumps(record, sort_keys=True, ensure_ascii=False))
        with self._lock:
            path = self.path("runs", run_id, "observations", f"{observation_id}.json")
            if path.exists() and not path.is_file():
                raise BoundaryError("observation_not_file")
            # Reuse unchanged archives; a damaged body can be replaced while its source exists.
            if not path.exists() or json.loads(path.read_text()) != record:
                atomic_json(path, record)
        return {
            "id": observation_id,
            "sha256": digest(record["content"]),
            "call_id": record["call_id"],
            "tool": record["tool"],
            "characters": len(record["content"]),
        }

    def read_observation(
        self,
        run_id: str,
        observation_id: str,
        *,
        offset: int = 0,
        limit: int = 1000,
        query: str = "",
    ) -> dict:
        """Read only a current-run tool observation, with bounded character pages."""
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", run_id):
            raise BoundaryError("invalid_observation_run")
        if not re.fullmatch(r"[a-f0-9]{64}", observation_id):
            raise BoundaryError("invalid_observation_id")
        if (
            type(offset) is not int
            or offset < 0
            or type(limit) is not int
            or not 1 <= limit <= 1000
            or not isinstance(query, str)
            or len(query) > 1000
        ):
            raise ValueError("invalid_observation_page")
        with self._lock:
            path = self.path("runs", run_id, "observations", f"{observation_id}.json")
            if path.exists() and not path.is_file():
                raise BoundaryError("observation_not_file")
            record = json.loads(path.read_text())
        if (
            not isinstance(record, dict)
            or set(record) != {"run_id", "call_id", "tool", "content"}
            or not all(isinstance(value, str) for value in record.values())
            or record.get("run_id") != run_id
            or digest(json.dumps(record, sort_keys=True, ensure_ascii=False)) != observation_id
        ):
            raise ValueError("observation_integrity_error")
        content = record["content"]
        if offset > len(content):
            raise ValueError("page_out_of_range")
        start = content.find(query, offset) if query else offset
        end = min(start + limit, len(content)) if start >= 0 else offset
        return {
            "id": observation_id,
            "sha256": digest(content),
            "tool": record["tool"],
            "call_id": record["call_id"],
            "offset": start if start >= 0 else offset,
            "next_offset": end if start >= 0 and end < len(content) else None,
            "total": len(content),
            "matched": start >= 0,
            "content": content[start:end] if start >= 0 else "",
        }

    def list_observations(
        self, run_id: str, *, offset: int = 0, limit: int = 10, query: str = ""
    ) -> dict:
        """Discover archived observations; pagination counts records, not characters."""
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", run_id):
            raise BoundaryError("invalid_observation_run")
        if (
            type(offset) is not int
            or offset < 0
            or type(limit) is not int
            or not 1 <= limit <= 20
            or not isinstance(query, str)
            or len(query) > 1000
        ):
            raise ValueError("invalid_observation_page")
        with self._lock:
            directory = self.path("runs", run_id, "observations")
            paths = [
                self.path("runs", run_id, "observations", path.name)
                for path in directory.glob("*.json")
                if re.fullmatch(r"[a-f0-9]{64}\.json", path.name)
            ]
            paths.sort(key=lambda path: path.name)
            if query:
                # ponytail: literal scan for bounded local runs; add an index only if measured
                # search latency warrants it. Never search checkpoint or raw trace payloads.
                matches = []
                for path in paths:
                    page = self.read_observation(run_id, path.stem, query=query, limit=200)
                    if page["matched"]:
                        matches.append(page)
                total = len(matches)
                pages = matches[offset : offset + limit]
            else:
                total = len(paths)
                # Read and verify only this page when no content search is requested.
                pages = [
                    self.read_observation(run_id, path.stem, limit=1)
                    for path in paths[offset : offset + limit]
                ]
            if offset > total:
                raise ValueError("page_out_of_range")
            observations = []
            for page in pages:
                reference = {key: page[key] for key in ("id", "sha256", "tool", "call_id")}
                reference["characters"] = page["total"]
                if query:
                    reference.update(match_offset=page["offset"], preview=page["content"])
                observations.append(reference)
            end = offset + len(observations)
        return {
            "observations": observations,
            "offset": offset,
            "next_offset": end if end < total else None,
            "total": total,
        }

    def emit(self, run_id: str, event: str, **payload) -> dict:
        with self._lock:
            path = self.path("runs", run_id, "trace.jsonl")
            path.parent.mkdir(parents=True, exist_ok=True)
            rows = self.events(run_id)
            row = self.redactor.value(
                {
                    "seq": len(rows) + 1,
                    "time": datetime.now(UTC).isoformat(),
                    "run_id": run_id,
                    "event": event,
                    "previous": rows[-1]["hash"] if rows else "",
                    **payload,
                }
            )
            row["hash"] = digest(json.dumps(row, sort_keys=True, ensure_ascii=False))
            with path.open("a") as stream:
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
                stream.flush()
            for listener in tuple(self.listeners):
                try:
                    listener(deepcopy(row))
                except Exception as exc:
                    # Observers cannot undo a committed event or interrupt an executed tool.
                    logging.getLogger(__name__).warning(
                        "Evidence listener failed: %s", type(exc).__name__
                    )
            return row

    def events(self, run_id: str) -> list[dict]:
        path = self.path("runs", run_id, "trace.jsonl")
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
