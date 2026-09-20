import json

import pytest

from aster.config import Settings
from aster.knowledge.memory import Memory
from aster.runtime import Runtime
from aster.schema import TaskSpec, ToolCall
from aster.storage.workspace import digest


def test_chinese_paraphrase_retrieves_grounded_excerpt_and_expires(tmp_path):
    text = "项目默认扫描 manual 目录，构建产物存放在 public。"
    source = tmp_path / "PROJECT.md"
    source.write_text(text)
    memory = Memory(tmp_path)
    item = memory.record(
        text,
        run_id="observed",
        evidence_id="event",
        observation=text,
        sources={source.name: digest(text)},
    )
    assert [row["id"] for row in memory.retrieve("默认目录是什么？")] == [item["id"]]
    assert memory.retrieve("数据库连接池配置") == []
    source.write_text("默认扫描 docs 目录。")
    assert memory.retrieve("默认目录是什么？") == []


async def test_followup_query_uses_recent_user_request_and_keeps_provenance(tmp_path):
    text = "文档构建产物存放在 public 目录。"
    (tmp_path / "PROJECT.md").write_text(text)
    runtime = Runtime(tmp_path, Settings(approval="auto", memory_enabled=True, auto_dream=False))
    runtime.start(TaskSpec(prompt="Inspect the project"))
    read = await runtime.step(ToolCall(name="read_file", arguments={"path": "PROJECT.md"}))
    saved = await runtime.step(
        ToolCall(
            name="memory_store",
            arguments={
                "text": text,
                "evidence_id": read.evidence_id,
            },
        )
    )
    assert saved.status == "success"
    runtime.finish("cancelled")
    runtime.continue_task("构建产物目录在哪？")
    messages, _, _ = runtime.prepare_request()
    context = messages[-1]["content"]
    assert text in context and read.evidence_id in context
    assert runtime.state.recalled_ids
    assert "evidence" in json.dumps(runtime.memory.state)


@pytest.mark.parametrize("enabled", [False, True])
def test_storage_guidance_is_conditioned_on_memory_capability(tmp_path, enabled):
    runtime = Runtime(tmp_path, Settings(memory_enabled=enabled))
    runtime.start(TaskSpec(prompt="Implement the requested feature"))
    messages, _, _ = runtime.prepare_request()
    system, observations = messages[0]["content"], messages[-1]["content"]
    assert ("durable project conventions" in system) is enabled
    assert ("Memory capability:" in system) is enabled
    assert "Memory capability:" not in observations
    if enabled:
        assert "read_file" in system and "exact excerpt" in system
        assert "Saving is optional" in system
