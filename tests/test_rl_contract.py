import pytest

from aster.schema import ACTION, Final, ToolCall


def test_action_contract_is_discriminated():
    assert isinstance(ACTION.validate_python({"kind": "final", "answer": "ok"}), Final)
    action = ACTION.validate_python(
        {"kind": "tool", "name": "read_file", "arguments": {"path": "a"}}
    )
    assert isinstance(action, ToolCall)
    with pytest.raises(ValueError):
        ACTION.validate_python({"kind": "tool", "name": ""})


@pytest.mark.asyncio
async def test_rl_step_accepts_wire_dict(tmp_path):
    from aster.config import Settings
    from aster.rl import AsterEnv
    from aster.schema import TaskSpec

    env = AsterEnv(tmp_path, Settings(approval="never"))
    env.reset(TaskSpec(prompt="inspect", max_steps=2))
    _observation, _reward, _terminated, _truncated, info = await env.step(
        {"kind": "tool", "name": "list_files", "arguments": {}}
    )
    assert info["outcome"]["status"] == "success"
