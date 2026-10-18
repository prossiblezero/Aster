"""Isolated rollout environment sharing Runtime; training algorithms live elsewhere."""

from __future__ import annotations

import copy
import inspect
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path

from ..config import Settings
from ..context import ContextBudgetError, ContextHistoryError
from ..core.runtime import Runtime
from ..schema import ACTION, Action, Final, TaskSpec, VerifierResult
from ..storage.workspace import Workspace


class AsterEnv:
    """Each reset clones the seed repository. A trusted verifier receives (root, state).

    Without an independent verifier the environment provides no success reward.
    The caller owns train/eval splits and verifier isolation; repository tests are
    observations only, never the source of the training reward.

    Rewards use an undiscounted episode return. Intermediate costs are normalized
    by max_turns: 0.05 for success, 0.5 for failure or rejected Final. Total action
    cost is therefore below 0.5. The terminal transition replaces its action cost
    with -1 for failure, +1 for independently verified success, or 0 for ungraded
    completion. Budget exhaustion also grades the last candidate. Delaying cannot
    avoid failure scoring; even a long successful episode outranks immediate failure.
    """

    def __init__(
        self, root: Path, settings: Settings | None = None, verifier: Callable | None = None
    ):
        self.root, self.settings, self.verifier = Path(root), settings or Settings(), verifier
        self.runtime: Runtime | None = None
        self._episode: tempfile.TemporaryDirectory | None = None
        self._stepping = False

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
        if self._stepping:
            raise RuntimeError("episode_busy")
        if self.runtime:
            if any(not w.future.done() for w in self.runtime.workers.workers.values()):
                raise RuntimeError(
                    "await runtime.workers.close() before resetting an active episode"
                )
            self.runtime.abort("episode_closed")
            self.runtime.sandbox.close()
        if self._episode:
            self._episode.cleanup()
        self._episode = None
        self.runtime = None

    async def step(self, action: Action | dict):
        if self._stepping:
            raise RuntimeError("episode_busy")
        self._stepping = True
        try:
            return await self._step(action)
        finally:
            self._stepping = False

    async def _step(self, action: Action | dict):
        if not self.runtime or not self.runtime.state:
            raise RuntimeError("reset() must be called first")
        state = self.runtime.state
        if state.terminal:
            raise RuntimeError("episode_finished: reset() before another step")
        action = ACTION.validate_python(action) if isinstance(action, dict) else action
        state.turn += 1  # An external policy decision consumes one turn, including Final.
        if isinstance(action, Final):
            # Do not freeze an owned worker when Final is only a request for review.
            self.runtime.workers.deliver()
            if not any(item.get("delivery") == "running" for item in state.pending_requirements):
                await self.runtime.workers.close()
            await self.runtime.recheck_preexisting()
            self.runtime.finalize(action.answer)
            outcome = None
            reward = -0.5
        else:
            outcome = await self.runtime.step(action)
            reward = -0.05 if outcome.status == "success" else -0.5
        reward /= state.task.max_turns
        if not state.terminal and (
            state.step >= state.task.max_steps or state.turn >= state.task.max_turns
        ):
            await self.runtime.workers.close()
            self.runtime.finish(
                "step_limit" if state.step >= state.task.max_steps else "turn_limit"
            )
        observation = self._observation()
        # Context construction is part of the environment transition, including budget stops.
        terminated = state.phase in {"completed", "failed"} or state.stop_reason in {
            "completion_requirements_failed",
            "stall_recovery_exhausted",
        }
        truncated = state.terminal and not terminated
        result = completion_result = None
        if state.terminal:
            await self.runtime.workers.close()
            reward, result, completion_result = await self._terminal_reward()
        safe_outcome = outcome.model_dump(exclude={"data"}) if outcome else None
        info = {
            "run_id": state.run_id,
            "step": state.step,
            "outcome": safe_outcome,
            "verification": state.verification.model_dump(),
            "reward_verification": result.model_dump() if result else None,
            "completion_verification": completion_result.model_dump()
            if completion_result
            else None,
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
            completion_verification=info["completion_verification"],
        )
        self.runtime.checkpoint()
        return observation, reward, terminated, truncated, info

    async def _terminal_reward(self) -> tuple[float, VerifierResult | None, VerifierResult]:
        state = self.runtime.state
        completion = self.runtime.verifier.final(state)
        result = None
        if self.verifier:
            try:
                result = self.verifier(self.runtime.workspace.root, state.model_copy(deep=True))
                if inspect.isawaitable(result):
                    result = await result
                if not isinstance(result, VerifierResult):
                    raise TypeError("verifier must return VerifierResult")
            except Exception as exc:
                result = VerifierResult(passed=False, reason=f"verifier_error:{type(exc).__name__}")
        eligible = state.phase == "completed" or state.stop_reason in {"step_limit", "turn_limit"}
        if not eligible or not completion.passed:
            return -1.0, result, completion
        if result is not None:
            return (1.0 if result.passed else -1.0), result, completion
        return (0.0 if state.phase == "completed" else -1.0), None, completion

    def _observation(self):
        state = self.runtime.state
        prompt, tools, report = [], [], None
        if not state.terminal:
            try:
                prompt, tools, report = self.runtime.prepare_request()
            except (ContextBudgetError, ContextHistoryError) as exc:
                state.feedback = str(exc)
                self.runtime.finish(
                    "context_history_invalid"
                    if isinstance(exc, ContextHistoryError)
                    else "context_budget_exceeded"
                )
            except (OSError, ValueError) as exc:
                state.feedback = f"context_archive_unavailable:{type(exc).__name__}"
                self.runtime.finish("context_archive_unavailable", failed=True)
        return {
            "model_input": copy.deepcopy(prompt),
            "context_policy": report.policy_version if report else "deterministic-v10",
            "context_tokens": report.estimated_tokens if report else 0,
            "task": state.task.model_dump(),
            "phase": state.phase,
            "turn": state.turn,
            "step": state.step,
            "messages": copy.deepcopy(state.messages),
            "working_memory": copy.deepcopy(state.working_memory),
            "mode": state.mode,
            "plan": state.plan,
            "todos": copy.deepcopy(state.todos),
            "active_skills": list(state.active_skills),
            "memory": self.runtime._memory_text(state.task.prompt),
            "tools": tools,
            "changed_paths": list(state.changed_paths),
            "verification": state.verification.model_dump(),
            "feedback": state.feedback,
            "workspace_hash": self.runtime.workspace.fingerprint(),
        }
