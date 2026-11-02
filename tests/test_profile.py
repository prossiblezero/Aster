import json

import pytest

from aster.context import profile
from aster.context.prompt import system_prompt
from aster.tools.schemas import CORE_TOOLS, SCHEMAS


def test_profile_covers_core_tools_and_renders_after_fixed_preamble():
    assert profile.EDITABLE_TOOLS == CORE_TOOLS
    for name in CORE_TOOLS:
        assert SCHEMAS[name][1] == profile.PROFILE["tools"][name]
    system = system_prompt(False, False, "", None)
    assert system.startswith(profile.SAFETY_PREAMBLE + " " + profile.PROFILE["policy"] + "\n")
    worker = system_prompt(False, False, "", None, worker=True)
    assert profile.PROFILE["main_policy"] not in worker


@pytest.mark.parametrize("change", [
    lambda p: p.pop("main_policy"),
    lambda p: p.update(extra="x"),
    lambda p: p.update(policy=""),
    lambda p: p.update(policy="x" * (profile.POLICY_CHARACTERS + 1)),
    lambda p: p["tools"].pop("read_file"),
    lambda p: p["tools"].update(memory_store="widen"),
    lambda p: p["tools"].update(run_shell="x" * (profile.TOOL_CHARACTERS + 1)),
])
def test_invalid_profiles_are_rejected(tmp_path, change):
    data = json.loads(profile.PROFILE_PATH.read_text())
    change(data)
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        profile.load(path)
