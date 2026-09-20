"""Independent read batches overlap while Runtime state stays ordered and cancellable."""

import asyncio
import json
import threading

import pytest

from aster.config import Settings
from aster.core.hooks import HookResult, Hooks
from aster.runtime import Runtime
from aster.safety.policy import Decision
from aster.schema import ModelReply, Outcome, TaskSpec, ToolCall
from aster.tools import ToolRegistry


def reads(count):
    return [
        ToolCall(name="read_file", arguments={"path": f"{i}.txt"}, call_id=f"c{i}")
        for i in range(count)
    ]


def runtime_for(tmp_path, calls, *, parallelism=2, max_steps=20, hooks=None):
    for i in range(5):
        (tmp_path / f"{i}.txt").write_text(f"value {i}\n")

    class Model:
        async def complete(self, *_):
            return ModelReply(calls=calls)

    settings = Settings(
        approval="auto", auto_dream=False, memory_enabled=False, max_workers=0, completion="off"
    ).model_copy(update={"read_parallelism": parallelism})
    runtime = Runtime(tmp_path, settings, model=Model(), hooks=hooks)
    runtime.start(TaskSpec(prompt="Inspect the files", max_steps=max_steps))
    return runtime


def results(runtime):
    return [
        json.loads(item["output"])
        for item in runtime._structured_history
        if item.get("type") == "function_call_output"
    ]


async def test_read_batch_really_overlaps_with_a_bound_and_ordered_state(tmp_path, monkeypatch):
    calls, lifecycle = reads(4), []
    main_thread = threading.get_ident()

    class Plugin:
        def before_tool(self, _state, call):
            lifecycle.append(("before", call.call_id, threading.get_ident()))

        def after_tool(self, _state, call, _outcome):
            lifecycle.append(("after", call.call_id, threading.get_ident()))

    runtime = runtime_for(tmp_path, calls, hooks=Hooks([Plugin()]))
    original = runtime.workspace.read
    barrier, lock = threading.Barrier(2), threading.Lock()
    active, maximum = 0, 0
    completed = []
    pairs = [threading.Event(), threading.Event()]

    def read(path):
        nonlocal active, maximum
        if threading.get_ident() == main_thread:
            return original(path)
        with lock:
            active += 1
            maximum = max(active, maximum)
        assert len([x for x in lifecycle if x[0] == "before"]) == 4
        barrier.wait(3)
        index = int(path.split(".")[0])
        if index % 2 == 0:
            assert pairs[index // 2].wait(3)
        content = original(path)
        with lock:
            completed.append(index)
            active -= 1
        if index % 2:
            pairs[index // 2].set()
        return content

    monkeypatch.setattr(runtime.workspace, "read", read)
    await runtime.turn()
    assert maximum == 2 and completed == [1, 0, 3, 2]
    assert [row["content"] for row in results(runtime)] == [f"value {i}" for i in range(4)]
    assert [x[:2] for x in lifecycle] == (
        [("before", f"c{i}") for i in range(4)] + [("after", f"c{i}") for i in range(4)]
    )
    assert all(thread == main_thread for _, _, thread in lifecycle)
    events = [
        e for e in runtime.evidence.events(runtime.state.run_id) if e["event"] == "tool_result"
    ]
    assert [e["step"] for e in events] == [1, 2, 3, 4]
    assert list(runtime.state.read_evidence) == [f"{i}.txt" for i in range(4)]


async def test_preflight_permissions_and_failure_preserve_other_results(
    tmp_path, monkeypatch
):
    calls = [
        *reads(2),
        ToolCall(name="read_file", arguments={"path": "missing.txt"}, call_id="missing"),
    ]
    runtime = runtime_for(tmp_path, calls)
    permission_order = []

    def permission(name, effect, arguments, settings, mode):
        permission_order.append(arguments["path"])
        return (
            Decision("deny", "test_denial")
            if arguments["path"] == "1.txt"
            else Decision("allow", "test")
        )

    monkeypatch.setattr("aster.core.runtime.permission", permission)
    await runtime.turn()
    observed = results(runtime)
    assert [row["status"] for row in observed] == ["success", "blocked", "error"]
    assert observed[1]["reason"] == "test_denial"
    assert permission_order == ["0.txt", "1.txt", "missing.txt"]
    assert runtime.state.step == 3 and runtime.state.used_call_ids == ["c0", "missing"]


async def test_all_three_read_tools_use_detached_execution(tmp_path, monkeypatch):
    calls = [
        reads(1)[0],
        ToolCall(name="search", arguments={"pattern": "value"}, call_id="search"),
        ToolCall(name="list_files", call_id="list"),
    ]
    runtime = runtime_for(tmp_path, calls, parallelism=3)
    original = ToolRegistry.execute
    barrier = threading.Barrier(3)
    main_thread = threading.get_ident()

    async def execute(self, call):
        assert threading.get_ident() != main_thread
        assert self.workspace.context.workflow is None
        barrier.wait(3)
        return await original(self, call)

    monkeypatch.setattr(ToolRegistry, "execute", execute)
    await runtime.turn()
    assert [row["status"] for row in results(runtime)] == ["success"] * 3
    assert "0.txt:1:value 0" in results(runtime)[1]["content"]


async def test_partial_step_budget_keeps_accepted_results_and_pairs_the_rest(tmp_path):
    runtime = runtime_for(tmp_path, reads(4), max_steps=2)
    await runtime.turn()
    observed = results(runtime)
    assert [row["status"] for row in observed] == ["success", "success", "blocked", "blocked"]
    assert [row["reason"] for row in observed][2:] == ["step_limit", "run_terminal"]
    assert runtime.state.step == 2 and runtime.state.stop_reason == "step_limit"


@pytest.mark.parametrize("parallelism,mixed", [(1, False), (2, True)])
async def test_disabled_or_mixed_batches_keep_serial_execution(
    tmp_path, monkeypatch, parallelism, mixed
):
    calls = reads(2)
    if mixed:
        calls.insert(
            1,
            ToolCall(
                name="write_file", arguments={"path": "new.txt", "content": "new"}, call_id="write"
            ),
        )
    runtime = runtime_for(tmp_path, calls, parallelism=parallelism)
    original = ToolRegistry.execute
    observed = []
    main_thread = threading.get_ident()

    async def execute(self, call):
        observed.append((call.call_id, threading.get_ident()))
        if call.name == "write_file":
            return Outcome(status="success")
        return await original(self, call)

    monkeypatch.setattr(ToolRegistry, "execute", execute)
    await runtime.turn()
    assert observed == [(call.call_id, main_thread) for call in calls]


async def test_cancel_waits_for_inflight_reads_and_never_publishes_late_results(
    tmp_path, monkeypatch
):
    runtime = runtime_for(tmp_path, reads(3))
    original = runtime.workspace.read
    started, release = threading.Barrier(3), threading.Event()
    main_thread = threading.get_ident()
    finished = []

    def read(path):
        if threading.get_ident() == main_thread:
            return original(path)
        started.wait(3)
        assert release.wait(3)
        finished.append(path)
        return original(path)

    monkeypatch.setattr(runtime.workspace, "read", read)
    task = asyncio.create_task(runtime.turn())
    await asyncio.to_thread(started.wait, 3)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    with pytest.raises(RuntimeError, match="runtime_busy"):
        await runtime.step(ToolCall(name="list_files"))
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(finished) == 2 and not runtime.state.read_evidence
    assert all(row["status"] == "cancelled" for row in results(runtime))
    assert not any(
        e["event"] == "tool_result" for e in runtime.evidence.events(runtime.state.run_id)
    )


async def test_mixed_batch_cancellation_keeps_an_already_completed_write(tmp_path, monkeypatch):
    calls = [
        ToolCall(
            name="write_file", arguments={"path": "new.txt", "content": "written"}, call_id="write"
        ),
        *reads(1),
    ]
    runtime = runtime_for(tmp_path, calls)
    started = asyncio.Event()
    original = ToolRegistry.execute

    async def execute(self, call):
        if call.name == "read_file":
            started.set()
            await asyncio.Event().wait()
        return await original(self, call)

    monkeypatch.setattr(ToolRegistry, "execute", execute)
    task = asyncio.create_task(runtime.turn())
    await asyncio.wait_for(started.wait(), 3)
    assert (tmp_path / "new.txt").read_text() == "written"
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert [row["status"] for row in results(runtime)] == ["success", "cancelled"]


async def test_external_edit_invalidates_parallel_read_evidence(tmp_path, monkeypatch):
    runtime = runtime_for(tmp_path, reads(2))
    original = runtime.workspace.read
    observed, release = threading.Barrier(3), threading.Event()
    main_thread = threading.get_ident()

    def read(path):
        text = original(path)
        if threading.get_ident() != main_thread:
            observed.wait(3)
            assert release.wait(3)
        return text

    monkeypatch.setattr(runtime.workspace, "read", read)
    task = asyncio.create_task(runtime.turn())
    await asyncio.to_thread(observed.wait, 3)
    (tmp_path / "0.txt").write_text("changed externally\n")
    release.set()
    await task
    assert results(runtime)[0]["content"] == "value 0"
    assert "0.txt" not in runtime.state.read_evidence
    assert "0.txt" not in runtime.state.working_memory
    outcome = await runtime.step(
        ToolCall(name="write_file", arguments={"path": "0.txt", "content": "unsafe"})
    )
    assert outcome.reason == "fresh_read_required"


async def test_preflight_hook_stop_prevents_all_parallel_work(tmp_path, monkeypatch):
    class Stop:
        def before_tool(self, _state, call):
            return HookResult("stop") if call.call_id == "c1" else HookResult()

    runtime = runtime_for(tmp_path, reads(3), hooks=Hooks([Stop()]))

    async def unexpected(*_):
        raise AssertionError("No read may start after a preflight hook stops the run")

    monkeypatch.setattr(ToolRegistry, "execute", unexpected)
    await runtime.turn()
    assert runtime.state.stop_reason == "hook_stop"
    assert all(row["status"] == "blocked" for row in results(runtime))


async def test_preflight_hook_cannot_turn_an_authorized_read_into_a_write(tmp_path):
    class Mutate:
        def before_tool(self, _state, call):
            call.name = "write_file"
            call.arguments = {"path": "must-not-exist.txt", "content": "unsafe"}

    runtime = runtime_for(tmp_path, reads(2), hooks=Hooks([Mutate()]))
    await runtime.turn()
    assert [row["content"] for row in results(runtime)] == ["value 0", "value 1"]
    assert not (tmp_path / "must-not-exist.txt").exists()


@pytest.mark.parametrize("parallelism", [0, 9])
def test_read_parallelism_has_an_explicit_upper_bound(parallelism):
    with pytest.raises(ValueError):
        Settings(read_parallelism=parallelism)
