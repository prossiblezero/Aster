"""Sourced project excerpts and derived knowledge, with explicit use feedback."""

from __future__ import annotations

import copy
import json
import re
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from ..safety.redaction import Redactor
from ..schema import Outcome
from ..storage.evidence import EvidenceStore, atomic_json
from ..storage.workspace import BoundaryError, Workspace, digest

# Both excerpts and derived claims retain source and content checks.
_SECRET = re.compile(
    r"(?i)\b(?:api[ _-]?key|password|access[ _-]?token|(?:client[ _-]?)?secret|credential)"
    r"\s*(?:is\b|[:=])\s*\S+"
    r"|\b(?:AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9-]{10,})"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----|\[REDACTED(?:_SECRET)?\]"
)
_TRANSIENT = re.compile(
    r"(?i)\b(?:current|this|next)\s+(?:task|goal|session|run|turn|step)\b"
    r"|\b(?:today|tomorrow|yesterday|next week|last week|in progress|work in progress)\b"
    r"|当前(?:任务|目标|会话|步骤)|本次(?:任务|运行)|本轮|这轮|今天|明天|昨天|下周|上周"
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
        self._defer_save = False
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
        if self._defer_save:
            return
        self.state["audit"] = self.state["audit"][-256:]
        atomic_json(self.path, self.state)

    @contextmanager
    def transaction(self):
        """Publish a validated batch once; failed admission leaves old lessons intact."""
        previous = copy.deepcopy(self.state)
        self._defer_save = True
        try:
            yield
            self._defer_save = False
            self.save()
        except BaseException:
            self.state = previous
            raise
        finally:
            self._defer_save = False

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
        kind: str = "excerpt",
        when: str = "",
        citations: list[dict] | None = None,
    ):
        """Runtime authenticates sources; derived knowledge is a model claim, not proof."""
        self.last_rejection = ""
        if not self.enabled:
            self.last_rejection = "disabled"
            return None
        text = text.strip()
        tags = list(dict.fromkeys(str(tag).strip() for tag in tags))[:16]
        when = when.strip()
        identity = normalized(text) if kind == "excerpt" else f"{kind}:{normalized(when)}:{normalized(text)}"
        lesson_id = digest(identity)[:16]
        lesson = next((x for x in self.state["lessons"] if x["id"] == lesson_id), None)
        sources = dict(sources or (lesson.get("sources", {}) if lesson else {}))
        reason = self._content_rejection(text + "\n" + when + "\n" + "\n".join(tags))
        if not reason and kind not in {"excerpt", "fact", "procedure"}:
            reason = "memory_kind"
        if not reason and kind != "excerpt" and (not when or len(when) > 200):
            reason = "memory_scope_required"
        if not reason and (
            not run_id
            or not evidence_id
            or evidence_type
            not in {"tool_result", "verifier_pass", "user_confirmed", "paired_eval", "derived"}
            or (kind != "excerpt" and not citations)
        ):
            reason = "provenance"
        if not reason and (
            not text
            or len(text) > 1000
            or not observation
            or (kind == "excerpt" and normalized(text) not in normalized(observation))
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
                "kind": kind,
                "when": when,
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
        # Adding evidence for the same knowledge must not erase its original authority.
        citations = list(citations or [])
        citations.extend(item for item in lesson["provenance"].get("citations", []) if item not in citations)
        lesson["provenance"] = {
            "run_id": run_id,
            "evidence_id": evidence_id,
            "evidence_type": evidence_type,
            "at": now(),
        }
        if citations:
            lesson["provenance"]["citations"] = citations
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

    def retrieve(self, query: str, limit: int = 3, *, track: bool = True):
        if not self.enabled or limit <= 0:
            return []
        # Adjacent Chinese pairs handle ordinary rephrasing without a tokenizer
        # dependency; this remains lexical retrieval, not semantic similarity.
        chinese = re.findall(r"[\u3400-\u9fff]+", query)
        words = set(re.findall(r"\w+", re.sub(r"[\u3400-\u9fff]+", " ", query.casefold())))
        words.update(span[index : index + 2] for span in chinese for index in range(len(span) - 1))
        ranked = []
        for item in self.state["lessons"]:
            if (
                item.get("inactive_reason")
                or item["contradictions"]
                or self._content_rejection(item["text"] + " " + item.get("when", "") + " " + " ".join(item["tags"]))
                or not self._sources_current(item.get("sources", {}))
            ):
                continue
            hay = (item["text"] + " " + item.get("when", "") + " " + " ".join(item["tags"])).casefold()
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
        if track:
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
        reason = (
            "memory_disabled"
            if not self.enabled
            else "evolution_disabled"
            if not self.evolution
            else "no_active_excerpts"
            if not any(not item.get("inactive_reason") for item in self.state["lessons"])
            else ""
        )
        if reason:
            return {"status": "skipped", "reason": reason, "checked": 0, "deactivated": 0}
        changed = 0
        checked = 0
        for item in self.state["lessons"]:
            reason = item.get("inactive_reason", "")
            if not reason:
                checked += 1
                reason = self._content_rejection(
                    item["text"] + " " + item.get("when", "") + " " + " ".join(item["tags"])
                )
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
        result = {"status": "completed", "reason": "", "checked": checked, "deactivated": changed}
        self.state["audit"].append({"event": "dream", **result})
        self.save()
        return result


def store_excerpt(
    memory: Memory,
    evidence: EvidenceStore,
    run_id: str,
    *,
    text: str,
    evidence_id: str,
    tags=(),
    lesson_id: str | None = None,
    kind: str = "excerpt",
    when: str = "",
    evidence_ids=(),
) -> Outcome:
    """Authenticate current-run sources for excerpts or scoped model-derived knowledge."""
    if lesson_id is not None and not any(
        item["id"] == lesson_id for item in memory.state["lessons"]
    ):
        return Outcome(status="blocked", reason="memory_not_found")
    references = list(dict.fromkeys([evidence_id, *evidence_ids]))
    if len(references) > 5 or (kind == "excerpt" and len(references) != 1):
        return Outcome(status="blocked", reason="memory_provenance")
    rows = evidence.events(run_id)
    observations, citations, sources = [], [], {}
    for reference in references:
        if reference == "user" and kind != "excerpt":
            event = next((row for row in reversed(rows)
                          if row["event"] in {"run_started", "run_continued"}), None)
            content = (event.get("feedback") if event and event["event"] == "run_continued"
                       else event.get("task", {}).get("prompt") if event else None)
            if not content:
                return Outcome(status="blocked", reason="memory_provenance")
            observations.append(content)
            citations.append({"run_id": run_id, "evidence_id": event["hash"], "source": "user",
                              "time": event["time"]})
            continue
        event = next((row for row in rows if row["event"] == "tool_result"
                      and reference in {row["hash"], row.get("outcome", {}).get("evidence_id")}), None)
        if event is None and re.fullmatch(r"[0-9a-f]{12,63}", reference):
            # Tool outputs show a 12-character evidence prefix; accept it only when unique.
            matches = [row for row in rows if row["event"] == "tool_result"
                       and (row.get("outcome", {}).get("evidence_id") or "").startswith(reference)]
            if len({row["outcome"]["evidence_id"] for row in matches}) == 1:
                event = matches[0]
                reference = event["outcome"]["evidence_id"]
        if event is None:
            return Outcome(status="blocked", reason="memory_provenance")
        observed, call = event["outcome"], event["call"]
        tool = call["name"]
        if tool == "read_file" and observed["status"] == "success":
            data = observed.get("data", {})
            if not data.get("source_sha256") or not data.get("path"):
                return Outcome(status="blocked", reason="memory_provenance")
            if not memory._sources_current({data["path"]: data["source_sha256"]}):
                return Outcome(status="blocked", reason="stale_source",
                               content="A cited file changed. Read it again and replace every stale citation.")
            sources[data["path"]] = data["source_sha256"]
        elif not (kind in {"fact", "procedure"} and tool in {"run_tests", "run_shell"}
                  and (observed["status"] == "success"
                       or (observed["status"] == "error"
                           and observed.get("exit_code") is not None))):
            return Outcome(status="blocked", reason="memory_provenance",
                           content="Excerpts require read_file; synthesized knowledge requires kind=fact or procedure and when. Cite actual current-run read_file, run_tests or run_shell results, or user.")
        observation = observed["content"] or observed.get("reason", "")
        if tool in {"run_tests", "run_shell"}:
            observation = call["arguments"]["command"] + "\n" + observation
        observations.append(observation)
        citations.append({"run_id": run_id, "evidence_id": reference, "source": tool,
                          "status": observed["status"], "time": event["time"]})
    lesson = memory.record(
        text,
        run_id=run_id,
        evidence_id=citations[0]["evidence_id"],
        tags=tags,
        observation="\n".join(observations),
        sources=sources,
        kind=kind, when=when, citations=citations,
        evidence_type="tool_result" if kind == "excerpt" else "derived",
    )
    if lesson and lesson_id is not None and lesson["id"] != lesson_id:
        memory.discard(lesson_id, reason="superseded")
    hints = {
        "transient": "Save only durable knowledge; omit temporary task instructions from text and when.",
        "unsupported_excerpt": "Copy exact read_file text without display line numbers, or use kind=fact/procedure with when for synthesis.",
        "memory_scope_required": "Provide when: the durable situation in which this knowledge applies (at most 200 characters).",
    }
    return Outcome(
        status="success" if lesson else "blocked",
        reason="" if lesson else memory.last_rejection,
        content=hints.get(memory.last_rejection, json.dumps(lesson or {}, ensure_ascii=False)),
        data=lesson or {},
    )
