"""Long workflow and skill text remains reachable through bounded model observations."""

import json

import pytest

from aster.config import Settings
from aster.context import ContextBuilder
from aster.runtime import Runtime
from aster.schema import ModelReply, RunState, TaskSpec, ToolCall
from aster.tools import text_page


def test_character_pages_cover_chinese_text_without_skipping_clipped_characters():
    source = "部署配置，遵守项目约束。\n" * 80 + "最后必须检查兼容性。"
    context = ContextBuilder(4096, 512, 64)
    chunks, offset = [], 0
    while offset is not None:
        page = text_page(source, offset, 512, context.count, context.tool_output_tokens)
        assert page.status == "success" and context.count(page.content) <= 64
        header, body = page.content.split("\n", 1)
        metadata = json.loads(header)
        assert metadata == page.data
        chunks.append(body)
        next_offset = metadata["next_offset"]
        assert next_offset is None or next_offset == offset + len(body) > offset
        offset = next_offset
    assert "".join(chunks) == source
    assert text_page(source, len(source) + 1, 10, context.count, 64).reason == "page_out_of_range"


def test_native_envelope_stays_valid_json_while_only_its_body_is_clipped():
    context = ContextBuilder(4096, 512, 64)
    record = {
        "status": "error",
        "reason": "tests_failed",
        "evidence_id": "abcde0123456789" * 5,
        "changed_paths": ["secret_parser.py"],
        "content": "详细失败说明。" * 1000,
    }
    history = [
        {"type": "function_call", "call_id": "one", "name": "run_tests", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "one", "output": json.dumps(record)},
    ]
    prompt, report = context.build(
        RunState(task=TaskSpec(prompt="Continue")), "Continue", structured_history=history
    )
    received = json.loads(prompt[-1]["output"])
    assert context.count(received["content"]) <= 64
    assert "tool output clipped" in received["content"]
    assert {key: value for key, value in received.items() if key != "content"} == {
        key: value for key, value in record.items() if key != "content"
    }
    assert report.estimated_tokens <= report.budget


@pytest.mark.parametrize("tool", ["todo_list", "skill_load"])
async def test_model_can_read_the_last_chinese_workflow_or_skill_page(tmp_path, tool):
    body = "核对中文部署说明。" * 300 + "尾部要求：检查最终兼容性。"
    settings = Settings(
        approval="auto",
        completion="off",
        context_tokens=4096,
        output_tokens=512,
        tool_output_tokens=64,
    )
    runtime = Runtime(tmp_path, settings)
    runtime.start(TaskSpec(prompt="Continue"))
    if tool == "todo_list":
        await runtime.step(ToolCall(name="plan_write", arguments={"content": body}))
        await runtime.step(ToolCall(name="todo_add", arguments={"content": body}))
        source = json.dumps({"plan": body, "todos": runtime.state.todos}, ensure_ascii=False)
        extra = {}
    else:
        directory = tmp_path / ".aster" / "skills"
        directory.mkdir()
        (directory / "guide.md").write_text(body)
        source, extra = body, {"name": "guide"}
    runtime.state.messages.clear()
    start = len(source) - 90

    class PageReader:
        pieces = []

        async def complete(self, prompt, _tools):
            outputs = [item for item in prompt if item.get("type") == "function_call_output"]
            offset = start
            if not outputs:
                assert "尾部要求：检查最终兼容性。" not in str(prompt)
            if outputs:
                envelope = json.loads(outputs[-1]["output"])
                assert envelope["status"] == "success"
                header, fragment = envelope["content"].split("\n", 1)
                metadata = json.loads(header)
                self.pieces.append(fragment)
                offset = metadata["next_offset"]
                if offset is None:
                    return ModelReply(text="Finished reading the requested pages.")
            return ModelReply(calls=[ToolCall(name=tool, arguments={**extra, "offset": offset})])

    reader = PageReader()
    runtime.model = reader
    state = await runtime.run()
    assert state.phase == "completed", state.feedback
    assert "".join(reader.pieces) == source[start:]
    assert "尾部要求：检查最终兼容性。" in "".join(reader.pieces)
    if tool == "skill_load":
        assert state.active_skills == ["guide"]
