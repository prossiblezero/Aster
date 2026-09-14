import asyncio
import json

from aster.config import Settings
from aster.runtime import Runtime
from aster.schema import ModelReply, TaskSpec


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


def test_tool_output_budget_is_applied_before_prompt(tmp_path):
    runtime = Runtime(tmp_path, Settings(tool_output_tokens=64))
    clipped = runtime.context._clip_tokens("x" * 1000)
    assert "[tool output clipped]" in clipped


def test_legacy_memory_working_state_is_migrated(tmp_path):
    path = tmp_path / ".aster" / "memory"
    path.mkdir(parents=True)
    (path / "state.json").write_text(json.dumps({"schema": 1, "working": {"old": {"summary": "x"}}, "lessons": [], "snapshots": {}, "audit": []}))
    from aster.memory import Memory

    memory = Memory(tmp_path)
    assert "working" not in memory.state
