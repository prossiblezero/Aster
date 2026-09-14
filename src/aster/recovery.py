"""Detect repeated non-progress outcomes with a bounded recovery window."""

from __future__ import annotations

import hashlib
import json
from collections import deque

from .schema import Outcome, RunState, ToolCall


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


def completion_feedback(state: RunState) -> str:
    missing = []
    task = state.task
    if task.require_observation and not state.observed:
        missing.append("inspect the workspace")
    if task.require_edit and not state.changed_paths:
        missing.append("make the requested edit")
    if task.require_verification and state.verification.state != "passed":
        missing.append("run a successful verification command")
    return "Completion requirements missing: " + ", ".join(missing) if missing else ""
