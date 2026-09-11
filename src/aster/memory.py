"""Layered memory with evidence-gated evolution and snapshot rollback."""

from __future__ import annotations

import copy
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from .evidence import atomic_json


def now():
    return datetime.now(UTC).isoformat(timespec="seconds")


class Memory:
    def __init__(self, root: Path, enabled: bool = True, evolution: bool = True):
        self.root = root / ".aster" / "memory"
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.root / "state.json"
        self.enabled, self.evolution = enabled, evolution
        self.state = (
            json.loads(self.path.read_text())
            if self.path.exists()
            else {"schema": 1, "working": {}, "lessons": [], "snapshots": {}, "audit": []}
        )

    def save(self):
        atomic_json(self.path, self.state)

    def working_section(self) -> str:
        if not self.enabled:
            return ""
        rows = [f"- {k}: {v['summary']}" for k, v in self.state["working"].items()]
        return "\n".join(rows)

    def remember_file(self, path: str, summary: str):
        if not self.enabled:
            return
        self.state["working"][path] = {"summary": summary[:500], "updated": now()}
        self.save()

    def record(
        self,
        text: str,
        *,
        run_id: str,
        evidence_id: str,
        tags=(),
        outcome="support",
        evidence_type="tool_result",
    ):
        if not self.enabled or not self.evolution or not text.strip():
            return None
        if (
            not run_id
            or not evidence_id
            or evidence_type
            not in {"tool_result", "verifier_pass", "user_confirmed", "paired_eval"}
        ):
            self.state["audit"].append({"event": "rejected", "reason": "provenance"})
            self.save()
            return None
        lesson_id = hashlib.sha256(text.casefold().encode()).hexdigest()[:16]
        lesson = next((x for x in self.state["lessons"] if x["id"] == lesson_id), None)
        if lesson is None:
            lesson = {
                "id": lesson_id,
                "text": text[:1000],
                "tags": list(tags),
                "support": 0,
                "contradictions": 0,
                "retrievals": 0,
                "successful_uses": 0,
                "failed_uses": 0,
                "provenance": {},
            }
            self.state["lessons"].append(lesson)
        key = "contradictions" if outcome in {"failure", "contradiction"} else "support"
        lesson[key] += 1
        lesson["provenance"] = {
            "run_id": run_id,
            "evidence_id": evidence_id,
            "evidence_type": evidence_type,
            "at": now(),
        }
        self.state["audit"].append(
            {
                "event": "record",
                "lesson_id": lesson_id,
                "run_id": run_id,
                "evidence_id": evidence_id,
                "outcome": outcome,
            }
        )
        self.state["lessons"] = self.state["lessons"][-64:]
        self.save()
        return copy.deepcopy(lesson)

    def retrieve(self, query: str, limit: int = 3):
        words = {w.casefold() for w in query.split() if w.strip()}
        ranked = []
        for item in self.state["lessons"]:
            hay = (item["text"] + " " + " ".join(item["tags"])).casefold()
            overlap = sum(w in hay for w in words)
            if overlap:
                confidence = (item["support"] + 1) / (item["support"] + item["contradictions"] + 2)
                ranked.append((overlap + confidence, item))
        ranked.sort(key=lambda x: x[0], reverse=True)
        selected = [copy.deepcopy(x[1]) for x in ranked[:limit]]
        for item in self.state["lessons"]:
            if item["id"] in {x["id"] for x in selected}:
                item["retrievals"] += 1
        self.save()
        return selected

    def record_use(self, lesson_ids, success: bool):
        for item in self.state["lessons"]:
            if item["id"] in set(lesson_ids):
                item["successful_uses" if success else "failed_uses"] += 1
        self.save()

    def snapshot(self):
        payload = copy.deepcopy(
            {"working": self.state["working"], "lessons": self.state["lessons"]}
        )
        ident = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]
        self.state["snapshots"][ident] = payload
        self.state["snapshots"] = dict(list(self.state["snapshots"].items())[-8:])
        self.save()
        return ident

    def rollback(self, ident: str) -> bool:
        snap = self.state["snapshots"].get(ident)
        if not snap:
            return False
        self.state["working"], self.state["lessons"] = snap["working"], snap["lessons"]
        self.state["audit"].append({"event": "rollback", "snapshot": ident})
        self.save()
        return True

    def dream(self):
        """Cheap deterministic consolidation; model-assisted dream is intentionally deferred."""
        before = len(self.state["lessons"])
        unique, seen = [], set()
        for item in self.state["lessons"]:
            if item["text"].casefold() in seen:
                continue
            seen.add(item["text"].casefold())
            unique.append(item)
        self.state["lessons"] = unique[-64:]
        self.state["audit"].append({"event": "dream", "before": before, "after": len(unique)})
        self.save()
