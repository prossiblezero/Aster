"""Detect repeated non-progress outcomes with a bounded recovery window."""

from __future__ import annotations

import hashlib
import json
from collections import deque

from ..schema import Outcome, RunState, ToolCall


class StallDetector:
    def __init__(self, window=4):
        self.items = deque(maxlen=max(4, window))

    def observe(self, call: ToolCall, outcome: Outcome) -> str:
        # Empty stdout is normal for successful edits. Progress breaks an old loop.
        if outcome.changed_paths:
            self.items.clear()
            return ""
        key = (
            call.name,
            hashlib.sha256(json.dumps(call.arguments, sort_keys=True).encode()).hexdigest(),
            hashlib.sha256(
                json.dumps([outcome.status, outcome.reason, outcome.content]).encode()
            ).hexdigest(),
        )
        self.items.append(key)
        if len(self.items) >= 2 and self.items[-1] == self.items[-2]:
            return "repeated_identical"
        if len(self.items) >= 4:
            recent = list(self.items)[-4:]
            if recent[0] == recent[2] and recent[1] == recent[3] and recent[0][:2] != recent[1][:2]:
                return "oscillation"
        if (
            outcome.status != "success"
            and len(self.items) >= 3
            and len({x[2] for x in list(self.items)[-3:]}) == 1
        ):
            return "output_loop"
        return ""


def completion_feedback(state: RunState, accepted: dict[str, str] | None = None) -> str:
    missing = []
    task = state.task
    if state.pending_requirements:
        missing.append("review open requirement todos " + ", ".join(
            item["id"] for item in state.pending_requirements
        ) + " via todo_update with the actual acceptance result; a worker handoff is not acceptance")
    if task.require_observation and not state.observed:
        missing.append("inspect the workspace")
    if task.require_edit and not state.changed_paths:
        missing.append("make the requested edit")
    accepted = accepted or {}
    failures = {k: v for k, v in state.verification_failures.items() if k not in accepted}
    if task.require_verification and failures:
        unexplained = {k: v for k, v in failures.items() if k not in state.preexisting_failures}
        identical = [k for k in failures if k in state.preexisting_failures]
        pending = list(unexplained.items())[:3]
        if pending:
            missing.append(
                "resolve previously failing tests: "
                + "; ".join(f"{test} (from {command})" for test, command in pending)
                + ". Rerun those cases after fixing them; passing a different selection does not "
                "resolve them. Report remaining failures honestly"
            )
        if identical:
            missing.append(
                "decide about tests that fail with the same error on the original files: "
                + "; ".join(identical[:3])
                + ". If the task requires one of them, fix it; otherwise rerun them if you edited since, "
                "then finish again and list them as pre-existing and unverified" + ("" if state.changed_paths else " after making the requested change")
            )
    elif task.require_verification and state.verification.state != "passed" and not accepted:
        missing.append(
            "run a successful verification command via run_tests "
            "(run_shell checks do not update formal verification)"
        )
    return "Completion requirements missing: " + ", ".join(missing) if missing else ""
