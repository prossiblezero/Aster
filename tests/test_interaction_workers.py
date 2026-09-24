"""Exercise human decisions and workers through the real Runtime boundary."""

import asyncio
import json
import os
import sys

import pytest

from aster.cli import Terminal
from aster.config import ExecRule, Settings
from aster.core.runtime import Runtime
from aster.core.workers import _scope
from aster.schema import ModelReply, TaskSpec, ToolCall
from aster.ui.app import AsterTui
from aster.ui.widgets import HumanPrompt


class CallThenFinish:
    def __init__(self, name, arguments):
        self.call = ToolCall(name=name, arguments=arguments)
        self.requests = []

    async def complete(self, messages, tools):
        self.requests.append(messages)
        if len(self.requests) == 1:
            call = self.call
            return ModelReply(
                calls=[call],
                raw=[
                    {
                        "type": "function_call",
                        "name": call.name,
                        "call_id": call.call_id,
                        "arguments": json.dumps(call.arguments),
                    }
                ],
            )
        return ModelReply(text="Finished.")


class PausedModel(CallThenFinish):
    def __init__(self):
        super().__init__("list_files", {})
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def complete(self, messages, tools):
        if not self.requests:
            self.entered.set()
            await self.release.wait()
        return await super().complete(messages, tools)


async def wait_until(predicate):
    async def poll():
        while not predicate():
            await asyncio.sleep(0.01)

    await asyncio.wait_for(poll(), 5)


@pytest.mark.asyncio
async def test_tui_denies_approval_without_blocking_event_loop(tmp_path):
    app = AsterTui(tmp_path, Settings(approval="ask", completion="off"))
    app.runtime.model = CallThenFinish("write_file", {"path": "new.txt", "content": "hello"})
    async with app.run_test() as pilot:
        app.query_one("#prompt").value = "demo"
        await pilot.press("enter")
        await wait_until(lambda: isinstance(app.screen, HumanPrompt))
        assert not (tmp_path / "new.txt").exists()
        assert app.screen.focused.id == "decline"
        await pilot.click("#decline")
        await wait_until(lambda: app.task_worker.is_finished)
        assert not (tmp_path / "new.txt").exists()
        events = app.runtime.evidence.events(app.runtime.state.run_id)
        assert any(
            row["event"] == "tool_result" and row["outcome"]["reason"] == "approval_denied"
            for row in events
        )
        assert not app.query_one("#prompt").disabled


@pytest.mark.asyncio
async def test_tui_question_answer_reaches_next_model_turn(tmp_path):
    app = AsterTui(tmp_path, Settings(approval="ask", completion="off"))
    model = CallThenFinish("ask_user", {"question": "Which color?"})
    app.runtime.model = model
    async with app.run_test() as pilot:
        app.query_one("#prompt").value = "demo"
        await pilot.press("enter")
        await wait_until(lambda: isinstance(app.screen, HumanPrompt))
        app.screen.query_one("#answer").value = "blue"
        await pilot.press("enter")
        await wait_until(lambda: app.task_worker.is_finished)
        assert len(model.requests) == 2
        assert "blue" in json.dumps(model.requests[-1])


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["ctrl+c", "ctrl+q"])
async def test_tui_cancel_closes_pending_human_prompt(tmp_path, key):
    app = AsterTui(tmp_path, Settings(approval="ask", completion="off"))
    app.runtime.model = CallThenFinish("write_file", {"path": "new.txt", "content": "hello"})
    async with app.run_test() as pilot:
        app.query_one("#prompt").value = "demo"
        await pilot.press("enter")
        await wait_until(lambda: isinstance(app.screen, HumanPrompt))
        await pilot.press(key)
        await pilot.pause()
        assert app.runtime.state.terminal
        assert not isinstance(app.screen, HumanPrompt)
        assert not (tmp_path / "new.txt").exists()
        assert app.is_running == (key == "ctrl+c")


@pytest.mark.asyncio
@pytest.mark.parametrize(("read_only", "mode"), [(True, "work"), (False, "plan")])
async def test_worker_permissions_never_exceed_parent(tmp_path, read_only, mode):
    parent = Runtime(
        tmp_path,
        Settings(
            read_only=read_only,
            approval="never",
            write_scope=["src"],
            network=False,
            max_workers=2,
            memory_enabled=True,
        ),
        model=PausedModel(),
    )
    parent.start(TaskSpec(prompt="demo", require_verification=True))
    parent.state.mode = mode
    response = await parent.workers.start("demo", mode="worker", write_scope=["src", "outside"])
    child = parent.workers.workers[response["worker_id"]].runtime
    assert child.settings.read_only
    assert child.settings.approval == "never"
    assert child.settings.network is False
    assert child.settings.write_scope == []
    assert child.settings.max_workers == 0
    assert child.settings.memory_enabled is False
    assert child.state.parent_run_id == parent.state.run_id
    assert not child.state.task.require_edit and not child.state.task.require_observation
    nested = await child.workers.start("nested")
    assert nested["reason"] == "worker_limit"
    stopped = await parent.workers.stop(response["worker_id"])
    assert stopped["status"] == "cancelled"
    assert child.state.terminal
    assert parent.workers.workers[response["worker_id"]].future.done()


def test_worker_scope_intersection_does_not_widen_permissions():
    assert _scope(["src", "docs"], ["src/allowed", "tests"]) == ["src/allowed"]
    assert _scope(["src/sub/file.py"], ["src"]) == ["src/sub/file.py"]
    assert _scope(["src"], []) == []
    for invalid in ("../outside", "/tmp", ".", "src/../outside"):
        with pytest.raises(ValueError, match="invalid_write_scope"):
            _scope([invalid], None)


@pytest.mark.asyncio
async def test_worker_steering_and_concurrency_slot_reuse(tmp_path):
    model = PausedModel()
    parent = Runtime(tmp_path, Settings(max_workers=1, completion="off"), model=model)
    parent.start(TaskSpec(prompt="demo"))
    started = await parent.workers.start("demo")
    worker_id = started["worker_id"]
    await model.entered.wait()
    rejected = await parent.workers.start("demo")
    assert rejected["reason"] == "worker_limit"
    queued = await parent.workers.message(worker_id, "Also check the license.")
    assert queued["status"] == "queued"
    assert not model.requests
    model.release.set()
    worker = parent.workers.workers[worker_id]
    await worker.future
    assert "Also check the license." in json.dumps(model.requests[-1])
    assert (await parent.workers.status(worker_id))["status"] == "completed"
    assert (await parent.workers.message(worker_id, "late"))["reason"] == "worker_finished"
    assert (await parent.workers.start("another task"))["status"] == "running"
    await parent.workers.close()


@pytest.mark.asyncio
async def test_worker_status_waits_without_cancelling_child(tmp_path):
    model = PausedModel()
    parent = Runtime(tmp_path, Settings(max_workers=1, completion="off"), model=model)
    parent.start(TaskSpec(prompt="demo"))
    started = await parent.workers.start("demo")
    worker_id = started["worker_id"]
    await model.entered.wait()

    timed_out = await parent.workers.status(worker_id, wait_seconds=0.01)
    assert timed_out["status"] == "running"
    assert not parent.workers.workers[worker_id].future.cancelled()

    waiter = asyncio.create_task(parent.workers.status(worker_id, wait_seconds=60))
    await asyncio.sleep(0)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert not parent.workers.workers[worker_id].future.done()

    model.release.set()
    finished = await parent.workers.status(worker_id, wait_seconds=1)
    assert finished["status"] == "completed"
    await parent.workers.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_child", [False, True])
async def test_waiting_tool_returns_child_failure_without_aborting_parent(tmp_path, cancel_child):
    entered, release = asyncio.Event(), asyncio.Event()

    class FailingModel:
        async def complete(self, *_):
            entered.set()
            await release.wait()
            raise RuntimeError("child failure")

    parent = Runtime(tmp_path, Settings(max_workers=1), model=FailingModel())
    parent.start(TaskSpec(prompt="demo"))
    started = await parent.workers.start("demo")
    worker_id = started["worker_id"]
    await entered.wait()
    waiting = asyncio.create_task(
        parent.step(
            ToolCall(
                name="worker_status",
                arguments={"worker_id": worker_id, "wait_seconds": 60},
            )
        )
    )
    await asyncio.sleep(0)
    assert not waiting.done()
    if cancel_child:
        await parent.workers.stop(worker_id)
    else:
        release.set()
    outcome = await asyncio.wait_for(waiting, 2)
    assert outcome.status == "success"
    assert outcome.data["status"] == ("cancelled" if cancel_child else "failed")
    assert outcome.data["verification"]["state"] == "missing"
    assert not parent.state.terminal
    await parent.workers.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("parent_verifies", [False, True])
async def test_worker_delivery_does_not_replace_parent_verification(tmp_path, parent_verifies):
    (tmp_path / "calc.py").write_text("def answer(): return 0\n")
    (tmp_path / "test_calc.py").write_text(
        "from calc import answer\ndef test_answer(): assert answer() == 42\n"
    )

    class ChildModel:
        def __init__(self):
            self.calls = iter(
                [
                    ToolCall(name="read_file", arguments={"path": "calc.py"}),
                    ToolCall(
                        name="write_file",
                        arguments={
                            "path": "calc.py",
                            "content": "def answer(): return 42\n",
                        },
                    ),
                ]
            )

        async def complete(self, *_):
            call = next(self.calls, None)
            return (
                ModelReply(calls=[call]) if call else ModelReply(text="Delivered; tests not run.")
            )

    parent = Runtime(
        tmp_path,
        Settings(approval="auto", max_workers=1, max_final_revisions=1),
        model=ChildModel(),
    )
    parent.start(
        TaskSpec(
            prompt="Implement and test answer() = 42.",
            require_verification=parent_verifies,
        )
    )
    prompt = parent.state.task.prompt
    started = await parent.step(
        ToolCall(
            name="worker_start",
            arguments={
                "task": "Implement answer() = 42 in calc.py; parent integration tests cover it.",
                "mode": "worker",
                "write_scope": ["calc.py"],
            },
        )
    )
    child = parent.workers.workers[started.data["worker_id"]].runtime
    assert child.state.task.require_verification == (not parent_verifies)
    assert child.state.task.require_edit and child.state.task.require_observation
    delivered = await parent.step(
        ToolCall(
            name="worker_status",
            arguments={
                "worker_id": started.data["worker_id"],
                "wait_seconds": 5,
            },
        )
    )
    assert delivered.data["status"] == ("completed" if parent_verifies else "stopped")
    assert delivered.data["verification"]["state"] == "missing"
    assert parent.state.task.prompt == prompt
    assert parent.state.task.require_verification == parent_verifies
    assert parent.state.changed_paths == ["calc.py"]
    await parent.step(ToolCall(name="read_file", arguments={"path": "calc.py"}))
    if parent_verifies:
        assert not parent.finalize("Delivered.").terminal
    assert parent.state.verification.state == "missing"
    result = await parent.step(
        ToolCall(
            name="run_tests",
            arguments={
                "command": "python -m pytest -q test_calc.py",
            },
        )
    )
    assert result.status == "success", result.content
    assert parent.finalize("Verified.").phase == "completed"
    await parent.workers.close()


@pytest.mark.asyncio
async def test_non_strict_parent_keeps_child_task_requirements(tmp_path):
    parent = Runtime(tmp_path, Settings(max_workers=1, completion="warn"), model=PausedModel())
    parent.start(TaskSpec(prompt="Implement and test"))
    result = await parent.workers.start("Implement and test")
    child = parent.workers.workers[result["worker_id"]].runtime
    assert child.state.task.require_verification
    await parent.workers.close()


@pytest.mark.asyncio
async def test_overlapping_worker_write_scopes_are_rejected(tmp_path):
    parent = Runtime(tmp_path, Settings(approval="auto", max_workers=2), model=PausedModel())
    parent.start(TaskSpec(prompt="demo"))
    assert (await parent.workers.start("demo", "worker", ["src"]))["status"] == "running"
    assert (await parent.workers.start("demo", "worker", ["src/core"]))[
        "reason"
    ] == "worker_scope_busy"
    assert (await parent.workers.start("demo", "worker", ["tests"]))["status"] == "running"
    await parent.workers.close()


@pytest.mark.asyncio
async def test_cli_stdin_partial_line_is_cancellable_and_buffered(monkeypatch):
    read_fd, write_fd = os.pipe()
    with os.fdopen(read_fd) as stream:
        monkeypatch.setattr(sys, "stdin", stream)
        terminal = Terminal(Settings())
        answer = asyncio.create_task(terminal.question("Choice?"))
        try:
            os.write(write_fd, b"part")
            await asyncio.sleep(0.02)
            assert not answer.done()
            answer.cancel()
            with pytest.raises(asyncio.CancelledError):
                await answer
            os.write(write_fd, b"ial\nnext\n")
            assert await terminal.question("Again?") == "partial"
            assert await terminal.question("Another?") == "next"
        finally:
            os.close(write_fd)


@pytest.mark.asyncio
async def test_compound_shell_cannot_bypass_hard_deny_with_human_approval(tmp_path):
    (tmp_path / "keep.txt").write_text("keep")
    runtime = Runtime(
        tmp_path,
        Settings(
            approval="ask",
            rules=[ExecRule(prefix=["rm"], decision="deny", reason="no_delete")],
        ),
        approval_callback=lambda *_: True,
    )
    runtime.start(TaskSpec(prompt="demo"))
    result = await runtime.step(
        ToolCall(name="run_shell", arguments={"command": "rm keep.txt; echo done"})
    )
    assert result.status == "blocked"
    assert result.reason == "compound_shell_with_deny_rules"
    assert (tmp_path / "keep.txt").read_text() == "keep"
