"""RL-compatible environment; no SFT/GRPO implementation lives here."""

from __future__ import annotations

import copy
from collections.abc import Callable
from pathlib import Path

from .config import Settings
from .runtime import Runtime
from .schema import ACTION, Action, Final, TaskSpec


class AsterEnv:
    def __init__(
        self, root: Path, settings: Settings | None = None, verifier: Callable | None = None
    ):
        self.root, self.settings, self.verifier = Path(root), settings or Settings(), verifier
        self.runtime: Runtime | None = None

    def reset(self, task: TaskSpec | str, *, seed: int | None = None):
        if isinstance(task, str):
            task = TaskSpec(prompt=task)
        self.runtime = Runtime(self.root, self.settings)
        state = self.runtime.start(task)
        return self._observation(), {"run_id": state.run_id, "seed": seed}

    async def step(self, action: Action | dict):
        if not self.runtime or not self.runtime.state:
            raise RuntimeError("reset() must be called first")
        parsed = self.runtime.state.task
        action = ACTION.validate_python(action) if isinstance(action, dict) else action
        if isinstance(action, Final):
            state = self.runtime.finalize(action.answer)
            outcome = None
        else:
            outcome = await self.runtime.step(action)
            state = self.runtime.state
        result = self.runtime.verifier.final(state)
        reward = self._reward(action, outcome, result)
        terminated = state.phase in {"completed", "failed"}
        truncated = state.phase == "stopped" or state.step >= parsed.max_steps
        safe_outcome = outcome.model_dump() if outcome else None
        if safe_outcome and safe_outcome.get("data", {}).get("image_data"):
            safe_outcome["data"]["image_data"] = "[omitted]"
        info = {
            "run_id": state.run_id,
            "step": state.step,
            "outcome": safe_outcome,
            "verification": state.verification.model_dump(),
            "stop_reason": state.stop_reason,
            "security_events": [
                e
                for e in self.runtime.evidence.events(state.run_id)
                if e["event"] in {"permission_decision", "stall_detected", "run_stopped"}
            ],
            "trace_path": str(self.runtime.evidence.path("runs", state.run_id, "trace.jsonl")),
        }
        return self._observation(), reward, terminated, truncated, info

    def _observation(self):
        state = self.runtime.state
        return {
            "phase": state.phase,
            "turn": state.turn,
            "step": state.step,
            "messages": copy.deepcopy(state.messages[-12:]),
            "changed_paths": state.changed_paths,
            "verification": state.verification.model_dump(),
            "feedback": state.feedback,
            "workspace_hash": self.runtime.workspace.fingerprint(),
        }

    @staticmethod
    def _reward(action, outcome, result):
        if isinstance(action, Final):
            return 1.0 if result.passed else -1.0
        if outcome is None:
            return -1.0
        if outcome.status == "success":
            return 0.05 + (0.1 if outcome.changed_paths else 0)
        if outcome.status == "blocked":
            return -0.2
        return -0.1
