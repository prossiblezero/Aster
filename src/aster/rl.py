"""Isolated rollout environment sharing Runtime; training algorithms live elsewhere."""

from __future__ import annotations

import copy
import inspect
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path

from .config import Settings
from .runtime import Runtime
from .schema import ACTION, Action, Final, TaskSpec, VerifierResult
from .tools import definitions
from .workspace import Workspace


class AsterEnv:
    """Each reset clones the seed repository. A trusted verifier receives (root, state).

    Without an independent verifier the environment provides no success reward.
    The caller owns train/eval splits and verifier isolation; repository tests are
    observations only, never the source of the training reward.
    """

    def __init__(
        self, root: Path, settings: Settings | None = None, verifier: Callable | None = None
    ):
        self.root, self.settings, self.verifier = Path(root), settings or Settings(), verifier
        self.runtime: Runtime | None = None
        self._episode: tempfile.TemporaryDirectory | None = None

    def reset(self, task: TaskSpec | str, *, seed: int | None = None):
        if isinstance(task, str):
            task = TaskSpec(prompt=task)
        self.close()
        self._episode = tempfile.TemporaryDirectory(prefix="aster-episode-")
        root = Path(self._episode.name)
        source = Workspace(self.root)
        for path in source.files():
            target = root / path.relative_to(source.root)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
        self.runtime = Runtime(root, self.settings)
        state = self.runtime.start(task)
        return self._observation(), {
            "run_id": state.run_id,
            "seed": seed,
            "workspace": str(root),
            "reward_verifier": bool(self.verifier),
        }

    def close(self):
        if self.runtime:
            if any(not w.future.done() for w in self.runtime.workers.workers.values()):
                raise RuntimeError(
                    "await runtime.workers.close() before resetting an active episode"
                )
            self.runtime.abort("episode_closed")
        if self._episode:
            self._episode.cleanup()
        self._episode = None
        self.runtime = None

    async def step(self, action: Action | dict):
        if not self.runtime or not self.runtime.state:
            raise RuntimeError("reset() must be called first")
        state = self.runtime.state
        if state.terminal:
            raise RuntimeError("episode_finished: reset() before another step")
        action = ACTION.validate_python(action) if isinstance(action, dict) else action
        state.turn += 1  # An external policy decision consumes one turn, including Final.
        result = None
        if isinstance(action, Final):
            # Freeze the candidate before either completion or external reward grading.
            await self.runtime.workers.close()
            self.runtime.finalize(action.answer)
            outcome = None
            reward = -0.01
            if state.phase == "completed" and self.verifier:
                try:
                    result = self.verifier(self.runtime.workspace.root, state.model_copy(deep=True))
                    if inspect.isawaitable(result):
                        result = await result
                    if not isinstance(result, VerifierResult):
                        raise TypeError("verifier must return VerifierResult")
                except Exception as exc:
                    result = VerifierResult(
                        passed=False, reason=f"verifier_error:{type(exc).__name__}"
                    )
                reward = 1.0 if result.passed else -1.0
            elif state.phase == "completed":
                reward = 0.0
        else:
            outcome = await self.runtime.step(action)
            reward = -0.01 if outcome.status == "success" else -0.2
        if not state.terminal and (
            state.step >= state.task.max_steps or state.turn >= state.task.max_turns
        ):
            self.runtime.finish(
                "step_limit" if state.step >= state.task.max_steps else "turn_limit"
            )
        terminated = state.phase in {"completed", "failed"}
        truncated = state.phase == "stopped"
        if state.terminal:
            await self.runtime.workers.close()
        safe_outcome = outcome.model_dump(exclude={"data"}) if outcome else None
        info = {
            "run_id": state.run_id,
            "step": state.step,
            "outcome": safe_outcome,
            "verification": state.verification.model_dump(),
            "reward_verification": result.model_dump() if result else None,
            "stop_reason": state.stop_reason,
            "trace_path": str(self.runtime.evidence.path("runs", state.run_id, "trace.jsonl")),
        }
        self.runtime.evidence.emit(
            state.run_id,
            "rl_transition",
            action=action.model_dump(),
            reward=reward,
            terminated=terminated,
            truncated=truncated,
            reward_verification=info["reward_verification"],
        )
        self.runtime.checkpoint()
        return self._observation(), reward, terminated, truncated, info

    def _observation(self):
        state = self.runtime.state
        return {
            "task": state.task.model_dump(),
            "phase": state.phase,
            "turn": state.turn,
            "step": state.step,
            "messages": copy.deepcopy(state.messages),
            "working_memory": copy.deepcopy(state.working_memory),
            "memory": self.runtime._memory_text(state.task.prompt),
            "tools": definitions(self.runtime._tool_names()),
            "changed_paths": list(state.changed_paths),
            "verification": state.verification.model_dump(),
            "feedback": state.feedback,
            "workspace_hash": self.runtime.workspace.fingerprint(),
        }
