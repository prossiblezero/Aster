"""Integration regressions found while reviewing terminal and protocol boundaries."""

import asyncio
import json

import pytest

from aster.config import Settings
from aster.context import ContextBuilder, history_groups
from aster.hooks import HookResult, Hooks
from aster.rl import AsterEnv
from aster.runtime import Runtime
from aster.schema import Final, ModelReply, TaskSpec, ToolCall, VerifierResult
from aster.workers import Worker


@pytest.mark.asyncio
async def test_after_tool_stop_reports_the_completed_edit(tmp_path):
    observed = []

    class StopAfterWrite:
        def after_tool(self, *_):
            return HookResult("stop", "stop after this edit")

        def stop(self, state):
            observed.append(list(state.changed_paths))
            return HookResult()

    runtime = Runtime(tmp_path, Settings(approval="auto"), hooks=Hooks([StopAfterWrite()]))
    runtime.start(TaskSpec(prompt="Create answer.txt"))
    outcome = await runtime.step(
        ToolCall(name="write_file", arguments={"path": "answer.txt", "content": "answer"})
    )
    report = json.loads(
        runtime.evidence.path("runs", runtime.state.run_id, "report.json").read_text()
    )
    checkpoint = json.loads(
        runtime.evidence.path("runs", runtime.state.run_id, "checkpoint.json").read_text()
    )
    assert outcome.status == "success" and runtime.state.terminal
    assert observed == [["answer.txt"]]
    assert report["changed_paths"] == checkpoint["state"]["changed_paths"] == ["answer.txt"]


@pytest.mark.asyncio
async def test_rl_stops_workers_before_async_reward_verification(tmp_path):
    (tmp_path / "answer.txt").write_text("before")
    observations = []

    async def verifier(root, _state):
        observations.append((root / "answer.txt").read_text())
        await asyncio.sleep(0.03)
        observations.append((root / "answer.txt").read_text())
        return VerifierResult(
            passed=observations == ["before", "before"], reason="stable_candidate"
        )

    env = AsterEnv(tmp_path, Settings(completion="off"), verifier)
    env.reset("finish")
    child = Runtime(env.runtime.workspace.root, Settings(completion="off"))
    child.start(TaskSpec(prompt="worker"))
    started = asyncio.Event()

    async def mutate_after_delay():
        started.set()
        await asyncio.sleep(0.01)
        (env.runtime.workspace.root / "answer.txt").write_text(
            "worker changed the graded candidate"
        )
        return child.finalize("done")

    future = asyncio.create_task(mutate_after_delay())
    env.runtime.workers.workers["w"] = Worker("w", "worker", "worker", child, future, [])
    await started.wait()
    try:
        _, reward, terminal, _, _ = await env.step(Final(answer="done"))
        assert terminal and reward == 1
        assert future.cancelled() and observations == ["before", "before"]
    finally:
        await env.runtime.workers.close()
        env.close()


@pytest.mark.asyncio
async def test_git_worktree_control_file_does_not_block_normal_edit(tmp_path):
    marker = "gitdir: /parent/repo/.git/worktrees/task"
    (tmp_path / ".git").write_text(marker)
    runtime = Runtime(tmp_path, Settings(approval="auto"))
    runtime.start(TaskSpec(prompt="Create answer.txt"))
    outcome = await runtime.step(
        ToolCall(name="write_file", arguments={"path": "answer.txt", "content": "answer"})
    )
    assert outcome.status == "success"
    assert (tmp_path / ".git").read_text() == marker
    assert ".git" not in runtime.workspace.snapshot()
    assert (tmp_path / "answer.txt").read_text() == "answer"


@pytest.mark.asyncio
async def test_responses_reasoning_message_parallel_calls_survive_next_turn(tmp_path):
    (tmp_path / "one.txt").write_text("one")
    (tmp_path / "two.txt").write_text("two")
    calls = [
        ToolCall(name="read_file", arguments={"path": name}, call_id=name)
        for name in ("one.txt", "two.txt")
    ]
    raw = [
        {"type": "reasoning", "id": "r1", "summary": [], "encrypted_content": "opaque"},
        {
            "type": "message",
            "role": "assistant",
            "content": [
                {"type": "output_text", "text": "Inspecting both files.", "annotations": []}
            ],
        },
        *[
            {
                "type": "function_call",
                "id": "fc_" + call.call_id,
                "call_id": call.call_id,
                "name": call.name,
                "arguments": json.dumps(call.arguments),
            }
            for call in calls
        ],
    ]

    class Model:
        def __init__(self):
            self.turn = 0

        async def complete(self, prompt, _tools):
            self.turn += 1
            if self.turn == 1:
                return ModelReply(text="Inspecting both files.", calls=calls, raw=raw)
            start = next(i for i, item in enumerate(prompt) if item.get("type") == "reasoning")
            exchanges = history_groups(prompt[start:])
            assert len(exchanges) == 1 and len(exchanges[0]) == 6
            assert exchanges[0][0]["encrypted_content"] == "opaque"
            return ModelReply(text="done", raw=[{"role": "assistant", "content": "done"}])

    runtime = Runtime(tmp_path, Settings(completion="off"), model=Model())
    state = await runtime.run(TaskSpec(prompt="Inspect these files."))
    assert state.phase == "completed"


def test_direct_step_observation_not_hidden_by_user_only_history():
    from aster.schema import RunState

    state = RunState(task=TaskSpec(prompt="Continue investigating."))
    state.messages = [
        {"role": "tool", "content": "compiler reported unsupported instruction", "native": False},
        {"role": "user", "content": "Change approach."},
    ]
    history = [{"role": "user", "content": "Change approach."}]
    prompt, _ = ContextBuilder(4096, 512).build(
        state, state.task.prompt, structured_history=history
    )
    assert "unsupported instruction" in str(prompt)
    assert str(prompt).count("Change approach.") == 1
