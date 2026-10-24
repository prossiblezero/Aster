"""Durable operation keys: replay settled results, never repeat uncertain effects."""

from __future__ import annotations

import hashlib
import json

from ..schema import Outcome


class OperationJournal:
    def __init__(self, evidence, session_id, call, safety_identity):
        key = call.idempotency_key or call.call_id
        key_hash = hashlib.sha256(key.encode()).hexdigest()
        self.evidence = evidence
        self.parts = ("operations", session_id, key_hash + ".json")
        self.fingerprint = hashlib.sha256(json.dumps(
            [call.name, call.arguments, safety_identity], sort_keys=True, ensure_ascii=False,
        ).encode()).hexdigest()

    def lookup(self):
        path = self.evidence.path(*self.parts)
        if not path.exists():
            return None
        saved = json.loads(path.read_text())
        if saved.get("fingerprint") != self.fingerprint:
            return Outcome(status="blocked", reason="idempotency_conflict",
                           content="This operation key was already used with different arguments or permissions.")
        if saved.get("state") != "settled":
            return Outcome(status="blocked", reason="idempotency_uncertain",
                           content="This operation may already have taken effect. Inspect its effects before starting a new operation.")
        outcome = Outcome.model_validate(saved["outcome"])
        # A receipt describes a past action; it must not manufacture fresh edits or test evidence.
        outcome.changed_paths, outcome.evidence_id, outcome.duration_ms = [], "", 0
        outcome.data = {"idempotency_replayed": True, "original_status": outcome.status}
        return outcome

    def reserve(self):
        self.evidence.save(self.parts, {"fingerprint": self.fingerprint, "state": "pending"})

    def settle(self, outcome):
        if outcome.data.get("safe_to_retry"):
            self.evidence.path(*self.parts).unlink(missing_ok=True)
        elif outcome.status not in {"timeout", "cancelled"} and not outcome.data.get("uncertain"):
            # A blocked publication can follow an already completed external effect.
            self.evidence.save(self.parts, {"fingerprint": self.fingerprint, "state": "settled",
                                            "outcome": outcome.model_dump(mode="json")})
