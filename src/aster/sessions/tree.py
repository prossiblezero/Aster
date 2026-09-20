"""Typed, immutable session entries; SQLite commits history and recovery points together.

Run checkpoints remain a backwards-compatible export. A turn groups a user request;
RunState.turn keeps its existing meaning (one model iteration).
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
import zlib
from contextlib import contextmanager
from copy import deepcopy
from uuid import uuid4

from ..messages import history_groups


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class SessionTree:
    def __init__(self, evidence):
        self.evidence = evidence
        self.cursor = None
        self.expected_head = None
        self.turn_id = None
        self._history = []
        self._identity = None
        self._snapshot = None
        self.pending = []

    @contextmanager
    def connect(self):
        # Recheck all SQLite paths, including sidecars, at every access.
        for name in ("sessions.sqlite3", "sessions.sqlite3-wal", "sessions.sqlite3-shm"):
            self.evidence.path(name)
        db = sqlite3.connect(self.evidence.path("sessions.sqlite3"), timeout=5)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA foreign_keys=ON")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, head TEXT, updated REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS entries (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL,
                    session_id TEXT NOT NULL REFERENCES sessions(id), parent_id TEXT,
                    kind TEXT NOT NULL, run_id TEXT NOT NULL, turn_id TEXT NOT NULL,
                    payload TEXT NOT NULL, created REAL NOT NULL,
                    FOREIGN KEY(parent_id) REFERENCES entries(id)
                );
                CREATE INDEX IF NOT EXISTS entries_session ON entries(session_id, seq);
                CREATE TABLE IF NOT EXISTS snapshots (
                    entry_id TEXT PRIMARY KEY REFERENCES entries(id), data BLOB NOT NULL
                );
            """)
            with db:
                yield db
        finally:
            db.close()

    def reset(self):
        self.cursor = self.expected_head = self.turn_id = None
        self._history, self._identity, self._snapshot, self.pending = [], None, None, []

    def attach(self, data):
        """Select a checkpoint parent without moving the shared head or rewriting old data."""
        self.reset()
        self.cursor = data.get("session_entry")
        self.turn_id = data.get("dialogue_id", data["state"]["run_id"])
        with self.connect() as db:
            row = db.execute("SELECT head FROM sessions WHERE id=?", (data["state"]["session_id"],)).fetchone()
            self.expected_head = row[0] if row else None
            if self.cursor:
                entry = db.execute("SELECT session_id FROM entries WHERE id=?", (self.cursor,)).fetchone()
                if entry is None:
                    self.cursor = None  # Portable v2 export without its optional tree database.
                elif entry[0] != data["state"]["session_id"]:
                    raise ValueError("session_entry_mismatched")
        if self.cursor:
            self._history = deepcopy(data["structured_history"])
            self._identity = deepcopy(data["runtime_identity"])
        else:
            self.pending.append(("legacy_import", {"run_id": data["state"]["run_id"]}))

    def commit(self, data):
        state = data["state"]
        sid, run_id = state["session_id"], state["run_id"]
        history = self.evidence.redact_history(data["structured_history"])
        snapshot = self.evidence.redactor.value(data, mask_fields=False)
        snapshot.pop("structured_history")
        snapshot.pop("session_entry", None)
        snapshot["dialogue_id"] = self.turn_id or run_id
        snapshot["session_history_sha256"] = hashlib.sha256(encode(history).encode()).hexdigest()
        packed = zlib.compress(encode(snapshot).encode())
        if packed == self._snapshot and history == self._history and not self.pending:
            return {**data, "session_entry": self.cursor, "dialogue_id": self.turn_id}
        identity = data["runtime_identity"]
        events = list(self.pending)
        if identity != self._identity:
            events.append(("model_change", {"previous": self._identity, "selected": identity}))
        if history[:len(self._history)] == self._history:
            events.extend(("message", {"message": item}) for item in history[len(self._history):])
        else:
            # Replacements are explicit events, never destructive edits to ancestors.
            events.append(("context_transform" if identity != self._identity else "compaction", {
                "history": history, "source_entry": self.cursor,
                "previous_messages": len(self._history), "retained_messages": len(history),
            }))
        events.append(("checkpoint", {
            "phase": state["phase"], "reason": state["stop_reason"],
            "model_turn": state["turn"], "steps": state["step"],
            "prompt": next((item["content"] for item in reversed(history)
                            if item.get("role") == "user" and isinstance(item.get("content"), str)),
                           state["task"]["prompt"]),
        }))
        parent = self.cursor
        now = time.time()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT OR IGNORE INTO sessions VALUES (?,?,NULL,?)", (sid, self.evidence.redactor.text(state["task"]["prompt"])[:120], now))
            head = db.execute("SELECT head FROM sessions WHERE id=?", (sid,)).fetchone()[0]
            if head != self.expected_head:
                committed = db.execute("SELECT data FROM snapshots WHERE entry_id=?", (head,)).fetchone()
                if committed and json.loads(zlib.decompress(committed[0])) == json.loads(encode(snapshot)) and not self.pending:
                    # Repeated cleanup of the same already-committed state is idempotent.
                    self.cursor = self.expected_head = head
                    self._history, self._identity, self._snapshot = history, deepcopy(identity), packed
                    return {**data, "session_entry": head, "dialogue_id": self.turn_id}
                raise RuntimeError("session_changed: reload before writing this session")
            for kind, payload in events:
                entry_id = uuid4().hex
                # Message/history payloads were redacted with native metadata preserved above.
                if kind not in {"message", "compaction", "context_transform"}:
                    payload = self.evidence.redactor.value(payload, mask_fields=False)
                db.execute("INSERT INTO entries(id,session_id,parent_id,kind,run_id,turn_id,payload,created) VALUES (?,?,?,?,?,?,?,?)",
                           (entry_id, sid, parent, kind, run_id, snapshot["dialogue_id"], encode(payload), now))
                parent = entry_id
            db.execute("INSERT INTO snapshots VALUES (?,?)", (parent, packed))
            db.execute("UPDATE sessions SET head=?,updated=? WHERE id=?", (parent, now, sid))
        self.cursor = self.expected_head = parent
        self.turn_id = snapshot["dialogue_id"]
        self._history, self._identity, self._snapshot = history, deepcopy(identity), packed
        self.pending.clear()
        return {**data, "session_entry": parent, "dialogue_id": self.turn_id}

    @staticmethod
    def resolve(db, reference, *, table="entries", session_id=None):
        if not re.fullmatch(r"[0-9a-f]{1,32}", reference):
            raise ValueError("请输入有效的记录编号或前缀")
        clause, args = "id >= ? AND id < ?", [reference, reference + "g"]
        if session_id is not None:
            clause += " AND session_id=?"
            args.append(session_id)
        rows = db.execute(f"SELECT * FROM {table} WHERE {clause} LIMIT 2", args).fetchall()
        if len(rows) != 1:
            raise ValueError("找不到唯一记录，请输入更长的编号")
        return dict(rows[0])

    def entries(self, session_id, query=""):
        with self.connect() as db:
            rows = db.execute("SELECT e.*, s.entry_id IS NOT NULL AS restorable FROM entries e LEFT JOIN snapshots s ON e.id=s.entry_id WHERE e.session_id=? ORDER BY e.seq", (session_id,)).fetchall()
        result = [{**dict(row), "payload": json.loads(row["payload"])} for row in rows]
        return [row for row in result if query.casefold() in encode(row["payload"]).casefold() or query in row["id"]]

    def sessions(self, query=""):
        with self.connect() as db:
            rows = db.execute("SELECT * FROM sessions ORDER BY updated DESC").fetchall()
        return [dict(row) for row in rows if query.casefold() in row["title"].casefold() or query in row["id"]]

    def rename(self, session_id, title):
        if not title.strip():
            raise ValueError("会话名称不能为空")
        with self.connect() as db:
            db.execute("UPDATE sessions SET title=? WHERE id=?", (self.evidence.redactor.text(title.strip())[:120], session_id))

    def session_head(self, reference):
        with self.connect() as db:
            return self.resolve(db, reference, table="sessions")["head"]

    def restore(self, reference, session_id=None):
        with self.connect() as db:
            target = self.resolve(db, reference, session_id=session_id)
            snapshot = db.execute("SELECT data FROM snapshots WHERE entry_id=?", (target["id"],)).fetchone()
            if snapshot is None:
                raise ValueError("请选择完整工具批次后的恢复点（树中的 ◇）")
            rows = db.execute("""WITH RECURSIVE path AS (
                SELECT * FROM entries WHERE id=? UNION ALL
                SELECT e.* FROM entries e JOIN path p ON e.id=p.parent_id
            ) SELECT * FROM path ORDER BY seq""", (target["id"],)).fetchall()
        history = []
        for row in rows:
            payload = json.loads(row["payload"])
            if row["kind"] == "message":
                history.append(payload["message"])
            elif row["kind"] in {"compaction", "context_transform"}:
                history = payload["history"]
        history_groups(history)
        data = json.loads(zlib.decompress(snapshot[0]))
        if data["state"]["session_id"] != target["session_id"] or data["state"]["run_id"] != target["run_id"]:
            raise ValueError("session_snapshot_identity_mismatch")
        expected = data.pop("session_history_sha256", None)
        if expected and expected != hashlib.sha256(encode(history).encode()).hexdigest():
            raise ValueError("session_history_integrity_error")
        return {**data, "structured_history": history, "session_entry": target["id"]}

    def excerpt(self, source, target):
        """Bounded extract of the departed path; no model call or memory promotion."""
        with self.connect() as db:
            def path(entry):
                return db.execute("""WITH RECURSIVE path AS (
                    SELECT * FROM entries WHERE id=? UNION ALL
                    SELECT e.* FROM entries e JOIN path p ON e.id=p.parent_id
                ) SELECT * FROM path ORDER BY seq""", (entry,)).fetchall()
            excluded = {row["id"] for row in path(target)}
            rows = [row for row in path(source) if row["id"] not in excluded]
        parts = []
        for row in rows:
            if row["kind"] == "turn_start":
                prompt = json.loads(row["payload"]).get("prompt")
                if prompt:
                    parts.append(f"[{row['id'][:12]} 用户请求] {prompt[:2000]}")
            if row["kind"] != "message":
                continue
            message = json.loads(row["payload"])["message"]
            content = message.get("content", message.get("output", ""))
            if isinstance(content, list):
                content = "\n".join(block.get("text", "") for block in content if isinstance(block, dict))
            if isinstance(content, str) and content:
                parts.append(f"[{row['id'][:12]} {message.get('role', message.get('type', '记录'))}] {content[:2000]}")
        text = "\n\n".join(parts)
        clipped = len(text) > 12000 or any(len(str(json.loads(row["payload"]))) > 2000 for row in rows if row["kind"] == "message")
        return {"source_entry": source, "target_entry": target, "method": "extractive",
                "text": text[-12000:], "truncated": clipped}
