import json

from aster.config import Settings
from aster.runtime import Runtime
from aster.schema import TaskSpec, ToolCall


def package(tmp_path):
    root = tmp_path / ".skills" / "review"
    (root / "references").mkdir(parents=True)
    (root / "SKILL.md").write_text(
        "---\nname: review\ndescription: Review CLI error behavior\n---\n"
        "BODY ONLY AFTER SELECTION\nRead references/checklist.md.\n"
    )
    (root / "references" / "checklist.md").write_text("RESOURCE ONLY AFTER REQUEST\n" * 50)


async def test_skill_metadata_is_discoverable_without_body_and_resources_do_not_activate(tmp_path):
    package(tmp_path)
    runtime = Runtime(tmp_path, Settings(memory_enabled=False))
    runtime.start(TaskSpec(prompt="Inspect this CLI"))
    prompt, _, _ = runtime.prepare_request()
    text = json.dumps(prompt)
    assert "Review CLI error behavior" in text and "references/checklist.md" in text
    assert "BODY ONLY AFTER SELECTION" not in text
    assert "RESOURCE ONLY AFTER REQUEST" not in text
    loaded = await runtime.step(
        ToolCall(
            name="skill_load",
            arguments={
                "name": "review",
                "resource": "references/checklist.md",
                "limit": 30,
            },
        )
    )
    assert loaded.status == "success", loaded
    assert "RESOURCE ONLY AFTER REQUEST" in loaded.content
    assert loaded.data["untrusted"] is True
    assert runtime.state.active_skills == []
    selected = await runtime.step(ToolCall(name="skill_load", arguments={"name": "review"}))
    assert selected.status == "success" and runtime.state.active_skills == ["review"]
    prompt, _, _ = runtime.prepare_request()
    assert "BODY ONLY AFTER SELECTION" in prompt[-1]["content"]
    # Resource content is only in the tool history, not silently made active policy.
    assert "RESOURCE ONLY AFTER REQUEST" not in prompt[-1]["content"]


async def test_skill_resource_pages_redact_before_offsets_and_reject_state_escape(tmp_path):
    package(tmp_path)
    secret = "private-skill-provider-credential"
    (tmp_path / ".skills/review/references/checklist.md").write_text("a" * 15 + secret + "尾部")
    runtime = Runtime(tmp_path, Settings(api_key=secret, memory_enabled=False))
    runtime.start(TaskSpec(prompt="Inspect"))
    pieces, offset = [], 0
    while offset is not None:
        result = await runtime.step(
            ToolCall(
                name="skill_load",
                arguments={
                    "name": "review",
                    "resource": "references/checklist.md",
                    "offset": offset,
                    "limit": 10,
                },
            )
        )
        assert result.status == "success", result
        header, body = result.content.split("\n", 1)
        pieces.append(body)
        offset = json.loads(header)["next_offset"]
    assert "".join(pieces) == "a" * 15 + "[REDACTED]尾部"
    invalid = await runtime.step(
        ToolCall(
            name="skill_load",
            arguments={
                "name": "review",
                "resource": "../../../../.aster/runs/checkpoint.json",
            },
        )
    )
    assert invalid.status != "success"
