import asyncio
import json

import pytest

from aster.config import Settings
from aster.context import history_groups
from aster.core.hooks import HookResult, Hooks
from aster.core.runtime import Runtime
from aster.schema import Final, ModelReply, TaskSpec, ToolCall, VerifierResult
from aster.training.env import AsterEnv


class StopCounter:
    def __init__(self):
        self.phases = []

    def stop(self, state):
        self.phases.append(state.phase)
        return HookResult()


@pytest.mark.asyncio
async def test_diff_resume_and_workflow_routes(tmp_path):
    (tmp_path / "a.txt").write_text("old\n")
    settings = Settings(approval="auto")
    runtime = Runtime(tmp_path, settings)
    state = runtime.start(TaskSpec(prompt="inspect and edit a.txt"))
    assert (
        await runtime.step(ToolCall(name="todo_add", arguments={"content": "fix a"}))
    ).status == "success"
    assert (
        await runtime.step(
            ToolCall(name="todo_update", arguments={"todo_id": "1", "status": "done"})
        )
    ).status == "success"
    assert (
        await runtime.step(ToolCall(name="plan_write", arguments={"content": "read then edit"}))
    ).status == "success"
    assert (await runtime.step(ToolCall(name="git_diff"))).content == ""
    await runtime.step(ToolCall(name="read_file", arguments={"path": "a.txt"}))
    await runtime.step(
        ToolCall(
            name="patch_file", arguments={"path": "a.txt", "old_text": "old", "new_text": "new"}
        )
    )
    resumed = Runtime(tmp_path, settings)
    resumed.resume(state.run_id)
    diff = await resumed.step(ToolCall(name="git_diff"))
    assert "-old\n+new\n" in diff.content
    assert resumed.state.plan == "read then edit"
    assert resumed.state.todos[0]["status"] == "done"


@pytest.mark.core
@pytest.mark.parametrize("interrupted", [False, True])
async def test_patch_review_contains_actual_diff_and_survives_resume(tmp_path, interrupted):
    (tmp_path / "app.py").write_text("value = 1\n")
    settings = Settings(approval="auto", memory_enabled=False)
    runtime = Runtime(tmp_path, settings)
    runtime.start(TaskSpec(prompt="Fix app.py and test the value", max_turns=20))
    await runtime.step(ToolCall(name="read_file", arguments={"path": "app.py"}))
    await runtime.step(ToolCall(name="patch_file", arguments={
        "path": "app.py", "old_text": "value = 1", "new_text": "value = 2",
    }))
    if interrupted:
        runtime.finish("provider_error")
        runtime.continue_task("Continue with the remaining work")
    await runtime.step(ToolCall(name="run_tests", arguments={
        "command": "pytest -q", "code": "from app import value\ndef test_value(): assert value == 2\n",
    }))
    state = runtime.finalize("Done")
    assert not state.terminal
    assert "-value = 1\n+value = 2" in state.feedback
    assert "Fix app.py and test the value" in state.feedback
    assert state.verification.evidence_id in state.feedback
    assert state.patch_review_requested and state.final_revisions == 1
    restored = Runtime(tmp_path, settings)
    restored.resume(state.run_id)
    assert restored.state.patch_review_requested
    assert restored.finalize("Reviewed callers and the reproduction; done").phase == "completed"
    resumed = restored.continue_task()
    assert resumed.patch_review_requested  # More budget for the same task is not a new review.
    restored.finalize("Done")
    completed_reference = restored.session_tree.cursor
    fresh = restored.continue_task("Update app.py again and test it")
    assert not fresh.patch_review_requested
    restored.checkout(completed_reference)
    assert not restored.state.patch_review_requested
    assert restored.workspace.diff(restored._initial_text, restored._initial_modes) == ""
    assert not restored.continue_task("Update app.py and test again").patch_review_requested
    runtime.sandbox.close()
    restored.sandbox.close()


def test_optional_patch_review_cannot_exhaust_a_valid_context(tmp_path):
    from aster.schema import Verification

    runtime = Runtime(tmp_path, Settings(memory_enabled=False, output_tokens=256))
    state = runtime.start(TaskSpec(prompt="Fix and test app.py", require_observation=False))
    (tmp_path / "app.py").write_text("value = 1\n" + "# opaque12345678901234567890\n" * 800)
    state.changed_paths = ["app.py"]
    state.verification = Verification(state="passed", workspace_hash=runtime.workspace.fingerprint())
    before = runtime._preview_context(state.model_copy(deep=True), [], runtime.settings, {})
    runtime.settings = runtime.settings.model_copy(update={
        "context_tokens": before.estimated_tokens + 256 + 200,
    })
    assert runtime.finalize("Done").phase == "completed"
    assert not state.patch_review_requested and not runtime._structured_history
    assert any(e["event"] == "patch_review_skipped" for e in runtime.evidence.events(state.run_id))


@pytest.mark.asyncio
async def test_stall_progress_and_recovery_persistence(tmp_path):
    runtime = Runtime(tmp_path, Settings(approval="auto"))
    runtime.start(TaskSpec(prompt="prepare files"))
    for i in range(4):
        result = await runtime.step(
            ToolCall(name="write_file", arguments={"path": f"{i}.txt", "content": "x"})
        )
        assert result.status == "success" and not runtime.state.terminal
    for _ in range(2):
        await runtime.step(ToolCall(name="read_file", arguments={"path": "0.txt"}))
    assert runtime._recovery_used == 1
    resumed = Runtime(tmp_path, runtime.settings)
    resumed.resume(runtime.state.run_id)
    assert resumed._recovery_used == 1
    for _ in range(2):
        await resumed.step(ToolCall(name="read_file", arguments={"path": "0.txt"}))
    assert resumed.state.stop_reason == "stall_recovery_exhausted"
    resumed.start(TaskSpec(prompt="new task"))
    assert resumed._recovery_used == 0 and not resumed.stall.items


@pytest.mark.asyncio
async def test_parallel_call_history_complete_after_hook_stop(tmp_path):
    class Model:
        async def complete(self, *_):
            return ModelReply(
                calls=[
                    ToolCall(name="list_files"),
                    ToolCall(name="search", arguments={"pattern": "x"}),
                ]
            )

    class StopAfter:
        def after_tool(self, *_):
            return HookResult("stop", "finished enough")

    counter = StopCounter()
    runtime = Runtime(tmp_path, Settings(), model=Model(), hooks=Hooks([StopAfter(), counter]))
    state = await runtime.run(TaskSpec(prompt="inspect"))
    checkpoint = json.loads(
        runtime.evidence.path("runs", state.run_id, "checkpoint.json").read_text()
    )
    assert len(history_groups(checkpoint["structured_history"])) >= 1
    results = [
        x for x in checkpoint["structured_history"] if x.get("type") == "function_call_output"
    ]
    assert len(results) == 2
    assert counter.phases == ["stopped"]
    runtime.finish("again")
    runtime.finalize("again")
    assert counter.phases == ["stopped"]


def test_success_stop_hook_once_and_artifacts(tmp_path):
    counter = StopCounter()
    runtime = Runtime(tmp_path, Settings(), hooks=Hooks([counter]))
    runtime.start(TaskSpec(prompt="ready", required_artifacts=["report.txt"]))
    assert runtime.finalize("done").phase == "stopped"
    (tmp_path / "report.txt").write_text("done")
    runtime.start(TaskSpec(prompt="ready", required_artifacts=["report.txt"]))
    assert runtime.finalize("done").phase == "completed"
    runtime.finalize("twice")
    runtime.finish("twice")
    assert counter.phases == ["stopped", "completed"]


@pytest.mark.asyncio
async def test_run_cancellation_hook_once(tmp_path):
    class Model:
        async def complete(self, *_):
            await asyncio.Event().wait()

    counter = StopCounter()
    runtime = Runtime(tmp_path, Settings(), model=Model(), hooks=Hooks([counter]))
    task = asyncio.create_task(runtime.run(TaskSpec(prompt="ready")))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert runtime.state.stop_reason == "cancelled"
    assert counter.phases == ["stopped"]


@pytest.mark.asyncio
async def test_read_only_and_plan_cannot_write_through_shell(tmp_path):
    for settings, mode in [
        (Settings(read_only=True, approval="auto"), "work"),
        (Settings(approval="auto"), "plan"),
    ]:
        runtime = Runtime(tmp_path, settings)
        runtime.start(TaskSpec(prompt="inspect"))
        runtime.state.mode = mode
        result = await runtime.step(
            ToolCall(name="run_shell", arguments={"command": "echo bad > escaped.txt"})
        )
        assert result.status == "blocked"
        assert not (tmp_path / "escaped.txt").exists()


@pytest.mark.asyncio
async def test_rl_isolated_reset_sparse_reward_and_trusted_verifier(tmp_path):
    (tmp_path / "answer.txt").write_text("old")

    def verifier(root, _state):
        return VerifierResult(
            passed=(root / "answer.txt").read_text() == "right", reason="independent_answer"
        )

    env = AsterEnv(tmp_path, Settings(approval="auto"), verifier)
    initial, info = env.reset(TaskSpec(prompt="edit answer.txt", max_steps=8))
    await env.step(ToolCall(name="read_file", arguments={"path": "answer.txt"}))
    _, reward, _, _, _ = await env.step(
        ToolCall(name="write_file", arguments={"path": "answer.txt", "content": "wrong"})
    )
    assert reward <= 0
    _, reward, terminal, truncated, info = await env.step(Final(answer="all tests pass"))
    assert reward == -1 and terminal and not truncated
    assert (tmp_path / "answer.txt").read_text() == "old"
    with pytest.raises(RuntimeError, match="episode_finished"):
        await env.step(Final(answer="try for another reward"))
    _, next_info = env.reset(TaskSpec(prompt="edit answer.txt"))
    assert (env.runtime.workspace.root / "answer.txt").read_text() == "old"
    assert next_info["run_id"] != info["run_id"]
    await env.step(ToolCall(name="read_file", arguments={"path": "answer.txt"}))
    await env.step(
        ToolCall(name="write_file", arguments={"path": "answer.txt", "content": "right"})
    )
    _, reward, terminal, truncated, _ = await env.step(Final(answer="done"))
    assert reward == 1 and terminal and not truncated
    env.close()


@pytest.mark.asyncio
async def test_rl_no_verifier_no_positive_reward_and_turn_truncation(tmp_path):
    env = AsterEnv(tmp_path)
    env.reset("ready")
    _, reward, terminal, _, _ = await env.step(Final(answer="done"))
    assert terminal and reward == 0
    env.reset(TaskSpec(prompt="edit missing file", max_turns=1))
    _, reward, terminal, truncated, _ = await env.step(Final(answer="pretend"))
    assert reward <= 0 and truncated and not terminal
    with pytest.raises(RuntimeError, match="episode_finished"):
        await env.step(Final(answer="pretend again"))
    env.close()


@pytest.mark.asyncio
async def test_memory_cannot_launder_its_own_sources(tmp_path):
    source = tmp_path / "config.txt"
    source.write_text("Cache TTL is 60 seconds.")
    runtime = Runtime(tmp_path, Settings(approval="auto", memory_enabled=True))
    runtime.start(TaskSpec(prompt="remember config"))
    observed = await runtime.step(ToolCall(name="read_file", arguments={"path": "config.txt"}))
    stored = await runtime.step(
        ToolCall(
            name="memory_store",
            arguments={"text": source.read_text(), "evidence_id": observed.evidence_id},
        )
    )
    assert stored.status == "success"
    recalled = await runtime.step(
        ToolCall(name="memory_retrieve", arguments={"query": "Cache TTL"})
    )
    source.write_text("Cache TTL is 120 seconds.")
    forged = await runtime.step(
        ToolCall(
            name="memory_store",
            arguments={"text": "Cache TTL is 60 seconds.", "evidence_id": recalled.evidence_id},
        )
    )
    assert forged.status == "blocked"
    assert runtime.memory.retrieve("Cache TTL") == []


@pytest.mark.asyncio
async def test_test_help_is_not_a_verification(tmp_path):
    runtime = Runtime(tmp_path, Settings(approval="auto"))
    runtime.start(TaskSpec(prompt="inspect"))
    result = await runtime.step(
        ToolCall(name="run_tests", arguments={"command": "python -m unittest -h"})
    )
    assert result.reason == "no_tests_collected"
    assert runtime.state.verification.state != "passed"
