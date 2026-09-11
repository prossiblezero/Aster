import pytest

from aster.schema import ACTION, Final, ToolCall


def test_action_contract_is_discriminated():
    assert isinstance(ACTION.validate_python({"kind": "final", "answer": "ok"}), Final)
    action = ACTION.validate_python({"kind": "tool", "name": "read_file", "arguments": {"path": "a"}})
    assert isinstance(action, ToolCall)
    with pytest.raises(ValueError):
        ACTION.validate_python({"kind": "tool", "name": ""})
