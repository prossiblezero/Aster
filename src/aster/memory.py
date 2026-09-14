"""Evidence-backed project excerpts, use feedback, and reversible consolidation."""

from __future__ import annotations

import copy
import json
import re
from datetime import UTC, datetime
from pathlib import Path

from .evidence import Redactor, atomic_json
from .workspace import BoundaryError, Workspace, digest

# Admission is deliberately conservative: exact observed excerpts, not inferred claims.
_SECRET = re.compile(
    r"(?i)\b(?:api[ _-]?key|password|access[ _-]?token|(?:client[ _-]?)?secret|credential)"
    r"\s*(?:is\b|[:=])\s*\S+"
    r"|\b(?:AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9-]{10,})"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----|\[REDACTED(?:_SECRET)?\]"
)
_TRANSIENT = re.compile(
    r"(?i)\b(?:current|this|next)\s+(?:task|goal|session|run|turn|step)\b"
    r"|\b(?:today|tomorrow|yesterday|next week|last week|in progress|work in progress)\b"
    r"|当前(?:任务|目标|会话|步骤)|本次(?:任务|运行)|今天|明天|昨天|下周|上周"
)
_UNSAFE = re.compile(
    r"(?i)(?:ignore|override|bypass)\s+(?:all\s+|the\s+|previous\s+)?"
    r"(?:instructions|rules|permissions|approval|sandbox|safety)"
    r"|(?:disable|turn off)\s+(?:the\s+)?(?:sandbox|approval|security)"
    r"|忽略(?:之前|所有)?(?:指令|规则)|绕过(?:审批|权限|沙箱)"
)


def now():
    return datetime.now(UTC).isoformat(timespec="seconds")


def normalized(text: str) -> str:
    return " ".join(text.split())


class Memory:
    def __init__(self, root: Path, enabled: bool = True, evolution: bool = True, *, secrets=()):
        self.workspace = Workspace(root)
        self.root = root / ".aster" / "memory"
        if any(
            path.is_symlink() for path in (root / ".aster", self.root, self.root / "state.json")
        ):
            raise BoundaryError("memory_symlink")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.root / "state.json"
        self.enabled, self.evolution = enabled, evolution
        self.redactor = Redactor(secrets)
        self.last_rejection = ""
        self.state = (
            json.loads(self.path.read_text())
            if self.path.exists()
            else {"schema": 2, "lessons": [], "snapshots": {}, "audit": []}
        )
        # Older stores lack excerpt admission; retain them for review, not automatic recall.
        migrated = self.state.get("schema", 1) < 2
        if migrated:
            for collection in [self.state, *self.state["snapshots"].values()]:
                for item in collection["lessons"]:
                    item["inactive_reason"] = "unverified_legacy"
            self.state["schema"] = 2
        # RunState is the only owner of session working memory.
        if self.state.pop("working", None) is not None or migrated:
            self.save()

    def save(self):
        self.state["audit"] = self.state["audit"][-256:]
        atomic_json(self.path, self.state)

    def working_section(self, working: dict | None = None) -> str:
        return "\n".join(f"- {k}: {v['summary']}" for k, v in (working or {}).items())

    def remember_file(self, path: str, summary: str, working: dict | None = None):
        if working is not None:
            working[path] = {"summary": summary[:500], "updated": now()}

    def _content_rejection(self, text: str) -> str:
        if self.redactor.text(text) != text or _SECRET.search(text):
            return "sensitive"
        if _TRANSIENT.search(text):
            return "transient"
        if _UNSAFE.search(text):
            return "unsafe_instruction"
        return ""

    def _sources_current(self, sources: dict[str, str]) -> bool:
        try:
            return all(digest(self.workspace.read(path)) == sha for path, sha in sources.items())
        except (OSError, ValueError, UnicodeError):
            return False

    def record(
        self,
        text: str,
        *,
        run_id: str,
        evidence_id: str,
        tags=(),
        outcome="support",
        evidence_type="tool_result",
        observation: str | None = None,
        sources: dict[str, str] | None = None,
    ):
        """Store a grounded excerpt; Runtime authenticates the cited trace event first."""
        self.last_rejection = ""
        if not self.enabled:
            self.last_rejection = "disabled"
            return None
        text = text.strip()
        tags = list(dict.fromkeys(str(tag).strip() for tag in tags))[:16]
        lesson_id = digest(normalized(text))[:16]
        lesson = next((x for x in self.state["lessons"] if x["id"] == lesson_id), None)
        sources = dict(sources or (lesson.get("sources", {}) if lesson else {}))
        reason = self._content_rejection(text + "\n" + "\n".join(tags))
        if not reason and (
            not run_id
            or not evidence_id
            or evidence_type
            not in {"tool_result", "verifier_pass", "user_confirmed", "paired_eval"}
        ):
            reason = "provenance"
        if not reason and (
            not text
            or len(text) > 1000
            or not observation
            or normalized(text) not in normalized(observation)
        ):
            reason = "unsupported_excerpt"
        if not reason and not self._sources_current(sources):
            reason = "stale_source"
        if reason:
            self.last_rejection = reason
            # Never persist rejected text, even in audit logs.
            self.state["audit"].append({"event": "rejected", "reason": reason})
            self.save()
            return None
        if lesson is None:
            lesson = {
                "id": lesson_id,
                "text": text,
                "tags": tags,
                "support": 0,
                "contradictions": 0,
                "retrievals": 0,
                "successful_uses": 0,
                "failed_uses": 0,
                "provenance": {},
                "sources": sources,
                "inactive_reason": "",
            }
            self.state["lessons"].append(lesson)
        key = "contradictions" if outcome in {"failure", "contradiction"} else "support"
        # Consecutive reuse of one event does not add support.
        if lesson["provenance"].get("evidence_id") != evidence_id:
            lesson[key] += 1
        lesson["sources"] = sources
        if key == "support" and lesson.get("inactive_reason") in {
            "stale_source",
            "unverified_legacy",
        }:
            lesson["inactive_reason"] = ""
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
        if not self.enabled or limit <= 0:
            return []
        words = set(re.findall(r"\w+", query.casefold()))
        ranked = []
        for item in self.state["lessons"]:
            if (
                item.get("inactive_reason")
                or item["contradictions"]
                or self._content_rejection(item["text"] + " " + " ".join(item["tags"]))
                or not self._sources_current(item.get("sources", {}))
            ):
                continue
            hay = (item["text"] + " " + " ".join(item["tags"])).casefold()
            overlap = sum(w in hay for w in words)
            if overlap:
                confidence = (item["support"] + 1) / (item["support"] + 2)
                reliability = (
                    (item["successful_uses"] + 1)
                    / (item["successful_uses"] + item["failed_uses"] + 2)
                    if self.evolution
                    else 1
                )
                ranked.append((overlap + confidence * reliability, item))
        ranked.sort(key=lambda x: x[0], reverse=True)
        selected = [copy.deepcopy(x[1]) for x in ranked[:limit]]
        selected_ids = {x["id"] for x in selected}
        for item in self.state["lessons"]:
            if item["id"] in selected_ids:
                item["retrievals"] += 1
        if selected_ids:
            self.save()
        return selected

    def record_use(self, lesson_ids, success: bool):
        if not self.enabled or not self.evolution:
            return
        wanted = set(lesson_ids)
        for item in self.state["lessons"]:
            if item["id"] in wanted:
                item["successful_uses" if success else "failed_uses"] += 1
        self.save()

    def discard(self, lesson_id: str, reason: str = "discarded") -> bool:
        if not self.enabled:
            return False
        for item in self.state["lessons"]:
            if item["id"] == lesson_id:
                item["contradictions"] += 1
                item["inactive_reason"] = reason
                self.save()
                return True
        return False

    def snapshot(self):
        payload = copy.deepcopy({"lessons": self.state.get("lessons", [])})
        ident = digest(json.dumps(payload, sort_keys=True))[:16]
        self.state["snapshots"][ident] = payload
        self.state["snapshots"] = dict(list(self.state["snapshots"].items())[-8:])
        self.save()
        return ident

    def rollback(self, ident: str) -> bool:
        snap = self.state["snapshots"].get(ident)
        if not snap:
            return False
        self.state["lessons"] = copy.deepcopy(snap["lessons"])
        self.state["audit"].append({"event": "rollback", "snapshot": ident})
        self.save()
        return True

    def dream(self):
        """Quarantine stale or repeatedly unsuccessful lessons without deleting evidence."""
        if not self.enabled or not self.evolution:
            return {"deactivated": 0}
        changed = 0
        for item in self.state["lessons"]:
            reason = item.get("inactive_reason", "")
            if not reason:
                reason = self._content_rejection(item["text"])
                if not reason and not self._sources_current(item.get("sources", {})):
                    reason = "stale_source"
                if not reason and item["contradictions"]:
                    reason = "contradicted"
                # ponytail: conservative deterministic feedback; no semantic LLM judge.
                if not reason and item["failed_uses"] >= max(2, item["successful_uses"] + 2):
                    reason = "repeated_failure"
                if reason:
                    item["inactive_reason"] = reason
                    changed += 1
        self.state["audit"].append({"event": "dream", "deactivated": changed})
        self.save()
        return {"deactivated": changed}
