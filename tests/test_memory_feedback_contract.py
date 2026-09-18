import json
from pathlib import Path

import pytest

from aster.config import Settings
from aster.rl import AsterEnv
from aster.schema import TaskSpec, ToolCall

CASES = json.loads((Path(__file__).parents[1] / "datasets/tool_contract_cases.json").read_text())


@pytest.mark.parametrize(
    "case",
    [case for case in CASES if case.get("memory_feedback") == "neutral"],
    ids=lambda case: case["id"],
)
async def test_manual_recall_survives_observation_and_setup_errors_do_not_punish_memory(
    tmp_path, case
):
    (tmp_path / "config.txt").write_text("Service port is 8123.")
    for name, content in case["files"].items():
        (tmp_path / name).write_text(content)
    env = AsterEnv(
        tmp_path,
        Settings(
            approval="auto",
            completion="off",
            memory_enabled=True,
            memory_evolution=True,
            auto_dream=False,
        ),
    )
    env.reset(TaskSpec(prompt="Answer", max_steps=20))
    try:
        _, _, _, _, info = await env.step(
            ToolCall(name="read_file", arguments={"path": "config.txt"})
        )
        await env.step(
            ToolCall(
                name="memory_store",
                arguments={
                    "text": "Service port is 8123.",
                    "evidence_id": info["outcome"]["evidence_id"],
                },
            )
        )
        _, _, _, _, info = await env.step(
            ToolCall(name="memory_retrieve", arguments={"query": "Service port"})
        )
        runtime = env.runtime
        lesson = runtime.memory.state["lessons"][0]
        assert runtime.state.recalled_ids == [lesson["id"]]
        _, _, _, _, info = await env.step(
            ToolCall(name="run_tests", arguments={"command": case["command"]})
        )
        assert info["outcome"]["reason"] == case["reason"]
        assert lesson["failed_uses"] == 0 and lesson["successful_uses"] == 0
        (runtime.workspace.root / "test_sample.py").write_text(
            "def test_ok():\n    assert 2 + 2 == 4\n"
        )
        _, _, _, _, info = await env.step(
            ToolCall(name="run_tests", arguments={"command": "python -m pytest -q"})
        )
        assert info["outcome"]["status"] == "success"
        assert lesson["successful_uses"] == 1 and lesson["failed_uses"] == 0
    finally:
        env.close()
