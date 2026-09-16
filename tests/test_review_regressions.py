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


@pytest.mark.asyncio
@pytest.mark.parametrize("memory_enabled", [True, False])
async def test_worker_reedit_invalidates_parent_cache_without_fake_progress(
    tmp_path, memory_enabled
):
    (tmp_path / "a.txt").write_text("old value")
    (tmp_path / "test_value.py").write_text(
        "from pathlib import Path\ndef test_value():\n"
        "    assert Path('a.txt').read_text().endswith('value')\n"
    )

    class ChildModel:
        def __init__(self):
            self.turn = 0

        async def complete(self, *_):
            self.turn += 1
            if self.turn == 1:
                return ModelReply(calls=[ToolCall(name="read_file", arguments={"path": "a.txt"})])
            if self.turn == 2:
                return ModelReply(
                    calls=[
                        ToolCall(
                            name="patch_file",
                            arguments={
                                "path": "a.txt",
                                "old_text": "parent value",
                                "new_text": "worker value",
                            },
                        )
                    ]
                )
            return ModelReply(text="done")

    runtime = Runtime(
        tmp_path,
        Settings(approval="auto", completion="off", memory_enabled=memory_enabled),
        model=ChildModel(),
    )
    runtime.start(TaskSpec(prompt="edit a.txt"))
    await runtime.step(ToolCall(name="read_file", arguments={"path": "a.txt"}))
    await runtime.step(
        ToolCall(
            name="patch_file",
            arguments={"path": "a.txt", "old_text": "old value", "new_text": "parent value"},
        )
    )
    await runtime.step(ToolCall(name="read_file", arguments={"path": "a.txt"}))
    assert runtime.state.working_memory["a.txt"]["summary"] == "parent value"
    await runtime.step(ToolCall(name="run_tests", arguments={"command": "pytest -q"}))
    await runtime.step(ToolCall(name="search", arguments={"pattern": "value"}))
    assert runtime.state.verification.state == "passed"
    result = await runtime.step(
        ToolCall(
            name="worker_start",
            arguments={"task": "edit a.txt", "mode": "worker", "write_scope": ["a.txt"]},
        )
    )
    worker_id = result.data["worker_id"]
    await runtime.workers.workers[worker_id].future
    status = await runtime.step(ToolCall(name="worker_status", arguments={"worker_id": worker_id}))
    assert (tmp_path / "a.txt").read_text() == "worker value"
    assert status.data["changed_paths"] == ["a.txt"] and status.changed_paths == []
    assert runtime.state.changed_paths == ["a.txt"]
    assert (
        "a.txt" not in runtime.state.working_memory and "a.txt" not in runtime.state.read_evidence
    )
    assert (
        "__search__" not in runtime.state.working_memory
        and "__run_tests__" not in runtime.state.working_memory
    )
    assert runtime.state.verification.state == "missing"
    await runtime.step(ToolCall(name="read_file", arguments={"path": "a.txt"}))
    status = await runtime.step(ToolCall(name="worker_status", arguments={"worker_id": worker_id}))
    assert status.changed_paths == []
    assert runtime.state.working_memory["a.txt"]["summary"] == "worker value"
    assert "a.txt" in runtime.state.read_evidence
    await runtime.workers.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("deleted", [False, True])
async def test_turn_refreshes_external_edits_before_building_prompt(tmp_path, deleted):
    (tmp_path / "a.txt").write_text("old fact")

    class InspectContext:
        async def complete(self, prompt, _tools):
            assert "a.txt" not in runtime.state.working_memory
            assert "a.txt" not in runtime.state.read_evidence
            assert not any("working_memory:" in item.get("content", "") for item in prompt)
            return ModelReply(text="done")

    runtime = Runtime(
        tmp_path, Settings(completion="off", memory_enabled=False), model=InspectContext()
    )
    runtime.start(TaskSpec(prompt="inspect a.txt"))
    await runtime.step(ToolCall(name="read_file", arguments={"path": "a.txt"}))
    if deleted:
        (tmp_path / "a.txt").unlink()
    else:
        (tmp_path / "a.txt").write_text("external edit")
    await runtime.turn()
    events = runtime.evidence.events(runtime.state.run_id)
    invalidated = next(event for event in events if event["event"] == "observations_invalidated")
    context = next(event for event in events if event["event"] == "context_built")
    assert invalidated["paths"] == ["a.txt"] and invalidated["seq"] < context["seq"]


@pytest.mark.asyncio
async def test_crlf_read_freshness_uses_same_text_hash_and_preserves_metadata(tmp_path):
    (tmp_path / "windows.txt").write_bytes(b"first line\r\nsecond line\r\n")
    runtime = Runtime(tmp_path, Settings(memory_enabled=False))
    runtime.start(TaskSpec(prompt="inspect windows.txt"))
    result = await runtime.step(ToolCall(name="read_file", arguments={"path": "./windows.txt"}))
    assert result.status == "success"
    before = dict(runtime.state.working_memory["./windows.txt"])
    read_hash = runtime.state.read_evidence["./windows.txt"]
    assert read_hash != runtime.workspace.snapshot()["windows.txt"]
    await runtime.step(ToolCall(name="list_files"))
    assert runtime.state.working_memory["./windows.txt"] == before
    assert runtime.state.read_evidence["./windows.txt"] == read_hash
    assert not any(
        event["event"] == "observations_invalidated"
        for event in runtime.evidence.events(runtime.state.run_id)
    )


@pytest.mark.asyncio
async def test_core_catalog_loads_optional_schema_then_executes_tool(tmp_path):
    from aster.tools import CORE_TOOLS, SCHEMAS

    class DiscoverAndUse:
        def __init__(self):
            self.turn = 0

        async def complete(self, _prompt, tools):
            self.turn += 1
            by_name = {tool["name"]: tool for tool in tools}
            if self.turn == 1:
                assert set(by_name) == CORE_TOOLS
                catalog = by_name["tool_load"]["parameters"]["properties"]["names"]["items"]["enum"]
                assert set(catalog) == set(SCHEMAS) - {"ask_user", "observation_read"}
                assert set(SCHEMAS) - CORE_TOOLS - {"ask_user", "observation_read"} <= set(catalog)
                return ModelReply(
                    calls=[ToolCall(name="tool_load", arguments={"names": ["todo_add"]})]
                )
            if self.turn == 2:
                assert "todo_add" in by_name and "todo_list" not in by_name
                assert "content" in by_name["todo_add"]["parameters"]["properties"]
                return ModelReply(
                    calls=[
                        ToolCall(
                            name="todo_add", arguments={"content": "Review the implementation"}
                        )
                    ]
                )
            return ModelReply(text="done")

    runtime = Runtime(
        tmp_path,
        Settings(tool_profile="core", completion="off", approval="auto"),
        model=DiscoverAndUse(),
    )
    state = await runtime.run(TaskSpec(prompt="Organize this work."))
    assert state.phase == "completed" and state.turn == 3
    assert state.loaded_tools == ["todo_add"]
    assert state.todos[0]["content"] == "Review the implementation"


@pytest.mark.asyncio
async def test_loading_unknown_tool_is_rejected_without_partial_loading(tmp_path):
    runtime = Runtime(tmp_path, Settings())
    runtime.start(TaskSpec(prompt="Organize this work."))
    result = await runtime.step(
        ToolCall(name="tool_load", arguments={"names": ["todo_add", "made_up_tool"]})
    )
    assert result.status == "blocked" and result.reason == "unknown_tool_name"
    assert "made_up_tool" in result.content
    assert runtime.state.loaded_tools == []
    assert "todo_add" not in runtime._tool_names()
