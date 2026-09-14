import pytest

from aster.config import Settings
from aster.hooks import HookResult, Hooks
from aster.memory import Memory
from aster.provider import ModelClient
from aster.rl import AsterEnv
from aster.runtime import Runtime
from aster.schema import ModelReply, TaskSpec, ToolCall
from aster.verifier import Verifier


class FakeModel:
    def __init__(self, replies):
        self.replies = iter(replies)

    async def complete(self, messages, tools):
        return next(self.replies)


def reply(*calls, text=""):
    return ModelReply(
        text=text, calls=list(calls), raw=[{"role": "assistant", "content": text}] if text else []
    )


@pytest.mark.asyncio
async def test_production_loop_write_then_verify_and_stale_verification(tmp_path):
    (tmp_path / "app.py").write_text("print('old')\n")
    (tmp_path / "test_app.py").write_text("def test_ok():\n    assert True\n")
    calls = [
        reply(ToolCall(name="read_file", arguments={"path": "app.py"}, call_id="r1")),
        reply(
            ToolCall(
                name="patch_file",
                arguments={"path": "app.py", "old_text": "old", "new_text": "new"},
                call_id="p1",
            )
        ),
        reply(
            ToolCall(
                name="run_tests",
                arguments={"command": "python -m pytest -q", "timeout": 10},
                call_id="t1",
            )
        ),
        reply(text="done"),
        reply(text="done after verification"),
    ]
    settings = Settings(approval="auto", completion="strict", sandbox="required")
    runtime = Runtime(tmp_path, settings, model=FakeModel(calls))
    state = await runtime.run(TaskSpec(prompt="Fix app.py and run tests", max_steps=10))
    assert state.phase == "completed"
    assert state.verification.state == "passed"
    assert (tmp_path / "app.py").read_text() == "print('new')\n"
    events = runtime.evidence.events(state.run_id)
    assert any(e["event"] == "verifier_result" for e in events)
    assert len(events) >= 12


@pytest.mark.asyncio
async def test_completion_strictly_rejects_final_without_edit(tmp_path):
    runtime = Runtime(
        tmp_path,
        Settings(approval="auto", completion="strict"),
        model=FakeModel(
            [
                reply(text="I would edit it later"),
                reply(text="Still not edited"),
                reply(text="No edit"),
            ]
        ),
    )
    state = await runtime.run(TaskSpec(prompt="Implement a change in app.py", max_steps=2))
    assert state.phase == "stopped"
    assert state.stop_reason == "completion_requirements_failed"


@pytest.mark.asyncio
async def test_rl_uses_same_boundary(tmp_path):
    env = AsterEnv(tmp_path, Settings(approval="never", sandbox="required"))
    observation, info = env.reset(TaskSpec(prompt="inspect", max_steps=2))
    observation, reward, terminated, truncated, info = await env.step(
        ToolCall(name="write_file", arguments={"path": "x.txt", "content": "x"}, call_id="rl1")
    )
    assert reward < 0 and not terminated
    assert info["outcome"]["status"] == "blocked"
    assert not (tmp_path / "x.txt").exists()


def test_memory_evolution_requires_evidence_and_rolls_back(tmp_path):
    memory = Memory(tmp_path)
    assert memory.record("unsafe shortcut", run_id="r", evidence_id="", tags=[]) is None
    lesson = memory.record(
        "run pytest after edits",
        run_id="r",
        evidence_id="e1",
        tags=["tests"],
        observation="run pytest after edits",
    )
    assert lesson and memory.retrieve("tests")
    snapshot = memory.snapshot()
    memory.record("another lesson", run_id="r", evidence_id="e2", observation="another lesson")
    assert memory.rollback(snapshot)
    assert not any(x["text"] == "another lesson" for x in memory.state["lessons"])


def test_disabled_memory_does_not_recall_persisted_lessons(tmp_path):
    memory = Memory(tmp_path)
    memory.record(
        "deploy ring is violet", run_id="r", evidence_id="e1", observation="deploy ring is violet"
    )
    before = memory.path.read_bytes()
    disabled = Memory(tmp_path, enabled=False)
    assert disabled.retrieve("deploy") == []
    assert disabled.path.read_bytes() == before
    assert Memory(tmp_path).retrieve("deploy")[0]["text"] == "deploy ring is violet"


def test_verifier_infer_respects_read_only_language(tmp_path):
    inferred = Verifier.infer("Inspect config.txt and tell me the mode. Do not modify files.")
    assert inferred == {
        "require_edit": False,
        "require_verification": False,
        "require_observation": True,
    }


def test_provider_reply_keeps_usage_for_cost_metrics():
    reply = ModelClient._responses_reply(
        {"model": "gpt-5.6-luna", "output_text": "ok", "output": []},
        {"input_tokens": 12, "output_tokens": 3, "total_tokens": 15},
    )
    assert reply.usage == {"input_tokens": 12, "output_tokens": 3, "total_tokens": 15}


@pytest.mark.asyncio
async def test_runtime_rejects_memory_evidence_not_in_trace(tmp_path):
    runtime = Runtime(tmp_path, Settings(approval="auto"), model=FakeModel([]))
    runtime.start(TaskSpec(prompt="remember"))
    outcome = await runtime.step(
        ToolCall(
            name="memory_store",
            arguments={"text": "untrusted", "evidence_id": "forged"},
            call_id="memory-1",
        )
    )
    assert outcome.status == "blocked"
    assert outcome.reason == "memory_provenance"


@pytest.mark.asyncio
async def test_read_file_updates_working_memory_and_context(tmp_path):
    (tmp_path / "facts.txt").write_text("deploy key is red\n")
    runtime = Runtime(tmp_path, Settings(approval="auto"), model=FakeModel([]))
    runtime.start(TaskSpec(prompt="inspect facts.txt"))
    outcome = await runtime.step(ToolCall(name="read_file", arguments={"path": "facts.txt"}))
    assert outcome.status == "success"
    assert runtime.state.working_memory["facts.txt"]["summary"] == "deploy key is red"
    prompt, _ = runtime.context.build(
        runtime.state,
        runtime.state.task.prompt,
        working_memory=runtime.memory.working_section(runtime.state.working_memory),
    )
    rendered = str(prompt)
    assert "deploy key is red" in rendered


@pytest.mark.asyncio
async def test_edit_invalidates_stale_working_memory(tmp_path):
    (tmp_path / "facts.txt").write_text("old fact\n")
    runtime = Runtime(tmp_path, Settings(approval="auto"), model=FakeModel([]))
    runtime.start(TaskSpec(prompt="edit facts.txt"))
    await runtime.step(ToolCall(name="read_file", arguments={"path": "facts.txt"}))
    await runtime.step(
        ToolCall(
            name="patch_file",
            arguments={"path": "facts.txt", "old_text": "old", "new_text": "new"},
        )
    )
    assert "facts.txt" not in runtime.state.working_memory


@pytest.mark.asyncio
async def test_resume_preserves_working_memory(tmp_path):
    (tmp_path / "facts.txt").write_text("persisted fact\n")
    runtime = Runtime(tmp_path, Settings(approval="auto"), model=FakeModel([]))
    state = runtime.start(TaskSpec(prompt="inspect facts.txt"))
    await runtime.step(ToolCall(name="read_file", arguments={"path": "facts.txt"}))
    run_id = state.run_id
    resumed = Runtime(tmp_path, Settings(approval="auto"))
    restored = resumed.resume(run_id)
    assert restored.working_memory["facts.txt"]["summary"] == "persisted fact"


@pytest.mark.asyncio
async def test_runtime_does_not_duplicate_tool_observation(tmp_path):
    (tmp_path / "facts.txt").write_text("one\n")
    runtime = Runtime(tmp_path, Settings(approval="auto"), model=FakeModel([]))
    runtime.start(TaskSpec(prompt="inspect facts.txt"))
    await runtime.step(ToolCall(name="read_file", arguments={"path": "facts.txt"}))
    assert sum(m["role"] == "tool" for m in runtime.state.messages) == 1


@pytest.mark.asyncio
async def test_injected_hooks_run_and_fail_closed(tmp_path):
    class Plugin:
        def __init__(self):
            self.seen = []

        def before_tool(self, _state, call):
            self.seen.append(("before", call.name))
            return HookResult()

        def after_tool(self, _state, call, _outcome):
            self.seen.append(("after", call.name))
            return HookResult(feedback="hook note")

    plugin = Plugin()
    runtime = Runtime(
        tmp_path,
        Settings(approval="auto"),
        model=FakeModel([]),
        hooks=Hooks([plugin]),
    )
    runtime.start(TaskSpec(prompt="list files"))
    outcome = await runtime.step(ToolCall(name="list_files", arguments={}))
    assert outcome.status == "success"
    assert plugin.seen == [("before", "list_files"), ("after", "list_files")]
    assert any(m["role"] == "user" and m["content"] == "hook note" for m in runtime.state.messages)
