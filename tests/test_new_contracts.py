import asyncio
import json

from aster.config import Settings
from aster.runtime import Runtime
from aster.schema import ModelReply, TaskSpec, ToolCall


class EmptyModel:
    async def complete(self, _messages, _tools):
        return ModelReply(text="done")


def test_max_turns_stops_model_loop(tmp_path):
    runtime = Runtime(tmp_path, Settings(completion="off"), model=EmptyModel())
    state = asyncio.run(runtime.run(TaskSpec(prompt="inspect", max_turns=1, max_steps=10)))
    assert state.turn == 1


def test_auto_dream_emits_event(tmp_path):
    runtime = Runtime(tmp_path, Settings(completion="off", auto_dream=True), model=EmptyModel())
    state = asyncio.run(runtime.run(TaskSpec(prompt="inspect", max_turns=1)))
    assert any(e["event"] == "memory_dream" for e in runtime.evidence.events(state.run_id))


async def test_large_tool_result_is_intact_until_the_request_exceeds_its_budget(tmp_path):
    text = "first evidence\n" * 1000 + "MIDDLE_FACT\n" + "last evidence\n" * 1000
    (tmp_path / "facts.txt").write_text(text)
    # Pin the reserve while this test manually shrinks only the context window.
    runtime = Runtime(tmp_path, Settings(approval="auto", output_tokens=2048))
    runtime.start(TaskSpec(prompt="Inspect the facts."))
    result = await runtime.step(
        ToolCall(name="read_file", arguments={"path": "facts.txt", "end": 2001})
    )
    assert runtime.context.count(result.content) > 1600
    prompt, report = runtime.context.build(
        runtime.state, runtime.state.task.prompt, structured_history=runtime._structured_history
    )
    assert json.loads(prompt[-1]["output"])["content"] == result.content
    assert not report.compacted
    runtime.context.budget = 4096
    prompt, report = runtime.context.build(
        runtime.state, runtime.state.task.prompt, structured_history=runtime._structured_history
    )
    assert report.compacted and report.estimated_tokens <= report.budget
    assert json.loads(prompt[-1]["output"])["content"] != result.content


def test_legacy_memory_working_state_is_migrated(tmp_path):
    path = tmp_path / ".aster" / "memory"
    path.mkdir(parents=True)
    (path / "state.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "working": {"old": {"summary": "x"}},
                "lessons": [],
                "snapshots": {},
                "audit": [],
            }
        )
    )
    from aster.memory import Memory

    memory = Memory(tmp_path)
    assert "working" not in memory.state
