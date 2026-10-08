import asyncio

import pytest

from aster.config import Settings
from aster.core.hooks import HookResult, Hooks
from aster.core.runtime import Runtime
from aster.core.workers import Worker
from aster.schema import ACTION, Final, TaskSpec, ToolCall, VerifierResult
from aster.training.env import AsterEnv

pytestmark = pytest.mark.core


def test_action_contract_is_discriminated():
    assert isinstance(ACTION.validate_python({"kind": "final", "answer": "ok"}), Final)
    action = ACTION.validate_python(
        {"kind": "tool", "name": "read_file", "arguments": {"path": "a"}}
    )
    assert isinstance(action, ToolCall)
    with pytest.raises(ValueError):
        ACTION.validate_python({"kind": "tool", "name": ""})


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_rl_step_accepts_wire_dict(tmp_path):
    env = AsterEnv(tmp_path, Settings(approval="never"))
    env.reset(TaskSpec(prompt="inspect", max_steps=2))
    _observation, _reward, _terminated, _truncated, info = await env.step(
        {"kind": "tool", "name": "list_files", "arguments": {}}
    )
    assert info["outcome"]["status"] == "success"
    env.close()


@pytest.mark.parametrize("limit", ["max_steps", "max_turns"])
@pytest.mark.parametrize("answer", ["right", "wrong"])
async def test_budget_grades_last_edit_without_a_final(tmp_path, limit, answer):
    graded = []

    def verifier(root, state):
        graded.append((state.phase, state.stop_reason))
        return VerifierResult(passed=(root / "answer.txt").read_text() == "right", reason="answer")

    env = AsterEnv(tmp_path, Settings(approval="auto"), verifier)
    env.reset(TaskSpec(prompt="Produce answer.txt", require_edit=True, **{limit: 1}))
    _, reward, terminated, truncated, info = await env.step(
        ToolCall(name="write_file", arguments={"path": "answer.txt", "content": answer})
    )
    assert reward == (1 if answer == "right" else -1)
    assert truncated and not terminated
    assert graded == [("stopped", "step_limit" if limit == "max_steps" else "turn_limit")]
    assert info["reward_verification"]["passed"] == (answer == "right")
    assert info["completion_verification"]["passed"]
    with pytest.raises(RuntimeError, match="episode_finished"):
        await env.step(Final(answer="double grade"))
    assert len(graded) == 1
    env.close()


async def test_delaying_failed_submission_has_strictly_lower_episode_return(tmp_path):
    (tmp_path / "answer.txt").write_text("wrong")

    def verifier(*_):
        return VerifierResult(passed=False, reason="wrong_answer")

    env = AsterEnv(tmp_path, verifier=verifier)
    env.reset(TaskSpec(prompt="Answer", max_turns=2))
    _, immediate, terminated, _, _ = await env.step(Final(answer="wrong"))
    assert terminated and immediate == -1
    env.reset(TaskSpec(prompt="Answer", max_turns=2))
    delayed = 0
    for tool in (
        ToolCall(name="list_files"),
        ToolCall(name="read_file", arguments={"path": "answer.txt"}),
    ):
        _, reward, _, truncated, _ = await env.step(tool)
        delayed += reward
    assert truncated and delayed < immediate
    env.close()


@pytest.mark.parametrize(
    "requirement",
    [
        {"require_observation": True},
        {"require_edit": True},
        {"require_verification": True},
        {"required_artifacts": ["missing.txt"]},
    ],
)
@pytest.mark.parametrize("completion", ["strict", "off"])
async def test_task_requirements_apply_at_truncation_even_if_external_verifier_passes(
    tmp_path, requirement, completion
):
    env = AsterEnv(
        tmp_path,
        Settings(completion=completion),
        lambda *_: VerifierResult(passed=True, reason="content_matches"),
    )
    env.reset(TaskSpec(prompt="Answer", max_turns=1, **requirement))
    _, reward, _, _, info = await env.step(Final(answer="unsupported claim"))
    assert reward == -1
    assert info["reward_verification"]["passed"]
    assert not info["completion_verification"]["passed"]
    env.close()


async def test_rejected_finals_cost_budget_and_completion_failure_terminates(tmp_path):
    env = AsterEnv(
        tmp_path,
        Settings(max_final_revisions=1),
        lambda *_: VerifierResult(passed=True, reason="weak_external_check"),
    )
    env.reset(TaskSpec(prompt="Answer", require_edit=True, max_turns=3))
    _, rejected, terminated, truncated, info = await env.step(Final(answer="premature"))
    assert rejected < 0 and not terminated and not truncated
    assert info["reward_verification"] is None
    _, failed, terminated, truncated, info = await env.step(Final(answer="premature again"))
    assert failed == -1 and terminated and not truncated
    assert info["stop_reason"] == "completion_requirements_failed"
    env.close()


async def test_stall_is_terminal_failure_and_cannot_receive_success_reward(tmp_path):
    env = AsterEnv(
        tmp_path,
        Settings(max_recovery=0),
        lambda *_: VerifierResult(passed=True, reason="weak_external_check"),
    )
    env.reset("Answer")
    await env.step(ToolCall(name="list_files"))
    _, reward, terminated, truncated, info = await env.step(ToolCall(name="list_files"))
    assert reward == -1 and terminated and not truncated
    assert info["stop_reason"] == "stall_recovery_exhausted"
    env.close()


async def test_hook_stop_is_truncated_but_never_success_reward(tmp_path):
    class Stop:
        def before_tool(self, *_):
            return HookResult("stop", "policy stop")

    env = AsterEnv(tmp_path, verifier=lambda *_: VerifierResult(passed=True, reason="weak_check"))
    env.reset("Answer")
    env.runtime.hooks = Hooks([Stop()])
    _, reward, terminated, truncated, info = await env.step(ToolCall(name="list_files"))
    assert reward == -1 and truncated and not terminated
    assert info["stop_reason"] == "hook_stop"
    env.close()


@pytest.mark.parametrize("invalid_return", [False, True])
async def test_verifier_errors_are_reported_without_reward(tmp_path, invalid_return):
    async def verifier(*_):
        if invalid_return:
            return True
        raise OSError("private grader detail")

    env = AsterEnv(tmp_path, verifier=verifier)
    env.reset(TaskSpec(prompt="Answer", max_turns=1))
    _, reward, _, truncated, info = await env.step(ToolCall(name="list_files"))
    assert truncated and reward == -1
    assert info["reward_verification"]["reason"] == (
        "verifier_error:TypeError" if invalid_return else "verifier_error:OSError"
    )
    env.close()


@pytest.mark.smoke
async def test_no_verifier_cannot_reward_budget_completion(tmp_path):
    env = AsterEnv(tmp_path)
    env.reset(TaskSpec(prompt="Answer", max_turns=1))
    _, reward, _, truncated, info = await env.step(ToolCall(name="list_files"))
    assert reward == -1 and truncated
    assert info["reward_verification"] is None
    env.close()


async def test_budget_cancels_workers_before_async_grading(tmp_path):
    (tmp_path / "answer.txt").write_text("stable")
    observations = []

    async def verifier(root, _state):
        observations.append((root / "answer.txt").read_text())
        await asyncio.sleep(0.02)
        observations.append((root / "answer.txt").read_text())
        return VerifierResult(passed=observations == ["stable", "stable"], reason="stable")

    env = AsterEnv(tmp_path, verifier=verifier)
    env.reset(TaskSpec(prompt="Answer", max_turns=1))
    child = Runtime(env.runtime.workspace.root, Settings())
    child.start(TaskSpec(prompt="Wait"))
    started = asyncio.Event()

    async def write_later():
        started.set()
        await asyncio.sleep(0.01)
        (child.workspace.root / "answer.txt").write_text("racing write")

    future = asyncio.create_task(write_later())
    env.runtime.workers.workers["w"] = Worker("w", "Wait", "worker", child, future, [])
    await started.wait()
    try:
        _, reward, _, truncated, _ = await env.step(ToolCall(name="list_files"))
        assert truncated and reward == 1
        assert future.cancelled() and observations == ["stable", "stable"]
    finally:
        await env.runtime.workers.close()
        env.close()


async def test_long_success_stays_above_immediate_failure(tmp_path):
    env = AsterEnv(
        tmp_path, Settings(max_recovery=5), lambda *_: VerifierResult(passed=True, reason="answer")
    )
    env.reset(TaskSpec(prompt="Answer", max_turns=500))
    total = 0
    # Distinct failures avoid the runtime's independent repeated-action stop.
    for index in range(20):
        _, reward, terminated, truncated, _ = await env.step(
            ToolCall(name="read_file", arguments={"path": f"missing-{index}.txt"})
        )
        assert not terminated and not truncated
        total += reward
    _, reward, _, _, _ = await env.step(Final(answer="answer"))
    assert total + reward > 0.5
    env.close()


def test_observation_exposes_planning_state_without_sharing_mutable_values(tmp_path):
    env = AsterEnv(tmp_path)
    env.reset("Answer")
    state = env.runtime.state
    state.mode, state.plan = "plan", "Review first"
    state.todos = [{"content": "Review", "status": "pending"}]
    state.active_skills = ["conventions"]
    observation = env._observation()
    assert observation["mode"] == "plan" and observation["plan"] == "Review first"
    observation["todos"][0]["content"] = "changed"
    observation["active_skills"].clear()
    assert state.todos[0]["content"] == "Review" and state.active_skills == ["conventions"]
    env.close()


async def test_pending_tool_blocks_concurrent_final_reset_and_close(tmp_path):
    entered, released = asyncio.Event(), asyncio.Event()
    graded = []

    async def approve(*_):
        entered.set()
        await released.wait()
        return True

    def verifier(root, _state):
        graded.append((root / "answer.txt").read_text())
        return VerifierResult(passed=graded == ["right"], reason="answer")

    env = AsterEnv(tmp_path, Settings(approval="ask"), verifier)
    env.reset(TaskSpec(prompt="Answer", max_turns=1))
    runtime = env.runtime
    runtime.approval_callback = approve
    pending = asyncio.create_task(
        env.step(ToolCall(name="write_file", arguments={"path": "answer.txt", "content": "right"}))
    )
    await asyncio.wait_for(entered.wait(), 1)
    try:
        with pytest.raises(RuntimeError, match="episode_busy"):
            await env.step(Final(answer="premature"))
        with pytest.raises(RuntimeError, match="episode_busy"):
            env.reset("other task")
        with pytest.raises(RuntimeError, match="episode_busy"):
            env.close()
        assert env.runtime is runtime and runtime.state.turn == 1 and not graded
        released.set()
        _, reward, _, truncated, _ = await pending
        assert truncated and reward == 1 and graded == ["right"]
    finally:
        released.set()
        await pending
        env.close()


async def test_episode_busy_guard_is_cleared_after_cancelled_tool(tmp_path):
    entered = asyncio.Event()

    async def approve(*_):
        entered.set()
        await asyncio.Event().wait()

    env = AsterEnv(tmp_path, Settings(approval="ask"))
    env.reset("Answer")
    env.runtime.approval_callback = approve
    pending = asyncio.create_task(
        env.step(ToolCall(name="write_file", arguments={"path": "answer.txt", "content": "right"}))
    )
    await asyncio.wait_for(entered.wait(), 1)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    env.reset("Fresh episode")
    _, reward, terminated, _, _ = await env.step(Final(answer="done"))
    assert terminated and reward == 0
    env.close()


async def test_episode_busy_guard_covers_async_reward_grading(tmp_path):
    entered, released = asyncio.Event(), asyncio.Event()

    async def verifier(*_):
        entered.set()
        await released.wait()
        return VerifierResult(passed=True, reason="answer")

    env = AsterEnv(tmp_path, verifier=verifier)
    env.reset("Answer")
    pending = asyncio.create_task(env.step(Final(answer="done")))
    await asyncio.wait_for(entered.wait(), 1)
    try:
        with pytest.raises(RuntimeError, match="episode_busy"):
            env.reset("Replacement")
        with pytest.raises(RuntimeError, match="episode_busy"):
            await env.step(Final(answer="double reward"))
        released.set()
        assert (await pending)[1] == 1
    finally:
        released.set()
        await pending
        env.close()
