"""Transitions stay within one run and cannot publish after an external stop."""

import asyncio
import json

import pytest

from aster.config import Settings
from aster.core.runtime import Runtime
from aster.schema import ModelReply, TaskSpec, ToolCall


def test_explicit_false_requirements_override_prompt_inference(tmp_path):
    runtime = Runtime(tmp_path, Settings())
    state = runtime.start(
        TaskSpec(
            prompt="Fix and test the implementation",
            require_edit=False,
            require_observation=False,
            require_verification=False,
        )
    )
    assert not state.task.require_edit
    assert not state.task.require_observation
    assert not state.task.require_verification
    previous = state
    inferred = runtime.start(TaskSpec(prompt="Fix and test the implementation"))
    assert previous.stop_reason == "superseded" and previous.terminal
    assert inferred.task.require_edit and inferred.task.require_verification


@pytest.mark.asyncio
async def test_worker_edits_count_without_polling_but_never_leak_into_the_next_run(tmp_path):
    class ChildModel:
        turns = 0

        async def complete(self, *_):
            self.turns += 1
            if self.turns == 1:
                return ModelReply(
                    calls=[
                        ToolCall(
                            name="write_file", arguments={"path": "child.txt", "content": "new"}
                        )
                    ]
                )
            return ModelReply(text="Done.")

    runtime = Runtime(
        tmp_path,
        Settings(approval="auto", max_final_revisions=0, max_workers=2),
        model=ChildModel(),
    )
    runtime.start(TaskSpec(prompt="demo", require_edit=True))
    started = await runtime.workers.start("demo", mode="worker", write_scope=["child.txt"])
    await runtime.workers.workers[started["worker_id"]].future
    assert runtime.finalize("Done.").phase == "completed"
    assert runtime.state.changed_paths == ["child.txt"]

    runtime.start(TaskSpec(prompt="demo", require_edit=True))
    before = runtime.workspace.fingerprint()
    result = await runtime.step(
        ToolCall(name="worker_status", arguments={"worker_id": started["worker_id"]})
    )
    assert result.status == "blocked" and result.data["status"] == "missing"
    assert runtime.workspace.fingerprint() == before and runtime.state.changed_paths == []
    assert runtime.finalize("No actual edit.").stop_reason == "completion_requirements_failed"


@pytest.mark.asyncio
@pytest.mark.parametrize("ignore_cancellation", [False, True])
async def test_abort_during_approval_never_executes_late_approved_write(
    tmp_path, ignore_cancellation
):
    entered = asyncio.Event()

    async def approve(*_):
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            if not ignore_cancellation:
                raise
        return True

    runtime = Runtime(tmp_path, Settings(), approval_callback=approve)
    runtime.start(TaskSpec(prompt="demo"))
    pending = asyncio.create_task(
        runtime.step(ToolCall(name="write_file", arguments={"path": "late.txt", "content": "x"}))
    )
    await entered.wait()
    runtime.abort()
    if ignore_cancellation:
        assert (await pending).reason == "run_terminal"
    else:
        with pytest.raises(asyncio.CancelledError):
            await pending
    assert runtime.state.stop_reason == "user_abort"
    assert not (tmp_path / "late.txt").exists()
    runtime.start(TaskSpec(prompt="A fresh run"))
    assert runtime.state.phase == "running"


@pytest.mark.asyncio
async def test_busy_runtime_rejects_other_entries_without_replacing_current_run(tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()

    async def approve(*_):
        entered.set()
        await release.wait()
        return False

    runtime = Runtime(tmp_path, Settings(), approval_callback=approve)
    state = runtime.start(TaskSpec(prompt="demo"))
    pending = asyncio.create_task(
        runtime.step(ToolCall(name="write_file", arguments={"path": "late.txt", "content": "x"}))
    )
    await entered.wait()
    try:
        with pytest.raises(RuntimeError, match="runtime_busy"):
            await runtime.step(ToolCall(name="list_files"))
        with pytest.raises(RuntimeError, match="runtime_busy"):
            await runtime.turn()
        with pytest.raises(RuntimeError, match="runtime_busy"):
            runtime.start(TaskSpec(prompt="Replacement"))
        with pytest.raises(RuntimeError, match="runtime_busy"):
            runtime.resume(state.run_id)
        with pytest.raises(RuntimeError, match="runtime_busy"):
            runtime.finalize("Premature final")
        assert runtime.state is state and not state.terminal
    finally:
        release.set()
        assert (await pending).reason == "approval_denied"


@pytest.mark.asyncio
async def test_stopped_model_response_cannot_add_actions_even_if_provider_ignores_cancellation(
    tmp_path,
):
    entered = asyncio.Event()

    class Model:
        async def complete(self, *_):
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                return ModelReply(
                    calls=[
                        ToolCall(name="write_file", arguments={"path": "late.txt", "content": "x"})
                    ]
                )

    runtime = Runtime(tmp_path, Settings(approval="auto"), model=Model())
    runtime.start(TaskSpec(prompt="demo"))
    pending = asyncio.create_task(runtime.turn())
    await entered.wait()
    runtime.abort()
    await pending
    assert runtime.state.step == 0 and runtime.state.stop_reason == "user_abort"
    assert runtime._structured_history == [] and not (tmp_path / "late.txt").exists()


def test_resume_rejects_active_state_and_invalid_checkpoint_without_replacing_state(tmp_path):
    runtime = Runtime(tmp_path, Settings())
    current = runtime.start(TaskSpec(prompt="demo"))
    with pytest.raises(RuntimeError, match="active_run"):
        runtime.resume(current.run_id)
    assert runtime.state is current
    runtime.abort()
    checkpoint = runtime.evidence.path("runs", current.run_id, "checkpoint.json")
    data = json.loads(checkpoint.read_text())
    data["state"]["phase"] = "running"
    del data["initial_text"]
    checkpoint.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="legacy_checkpoint_missing_diff_baseline"):
        runtime.resume(current.run_id)
    assert runtime.state is current and current.terminal


@pytest.mark.asyncio
async def test_finish_cancels_live_worker_and_disallows_spawning_after_terminal(tmp_path):
    entered = asyncio.Event()

    class Model:
        async def complete(self, *_):
            entered.set()
            await asyncio.Event().wait()

    runtime = Runtime(tmp_path, Settings(max_workers=2), model=Model())
    state = runtime.start(TaskSpec(prompt="demo"))
    response = await runtime.workers.start("demo")
    await entered.wait()
    with pytest.raises(RuntimeError, match="runtime_busy"):
        runtime.resume(state.run_id)
    runtime.finalize("No more work.")
    await runtime.workers.close()
    worker = runtime.workers.workers[response["worker_id"]]
    assert worker.future.cancelled() and worker.runtime.state.terminal
    assert (await runtime.workers.start("late"))["reason"] == "run_terminal"
