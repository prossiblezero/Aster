"""Tool results answer the model's next question so it does not spend a turn asking it."""

import json

import pytest

from aster.config import Settings
from aster.core.runtime import Runtime
from aster.schema import TaskSpec, ToolCall
from aster.tools.schemas import TextPage
from aster.tools.workspace import closest_lines, edit_preview

pytestmark = pytest.mark.core

SOURCE = "def a():\n    return 1\n\n\ndef b():\n    return 2\n\nx = 1\nx = 1\n"


async def runtime_with(tmp_path):
    (tmp_path / "m.py").write_text(SOURCE)
    runtime = Runtime(tmp_path, Settings(approval="auto", memory_enabled=False))
    runtime.start(TaskSpec(prompt="edit m.py"))
    await runtime.step(ToolCall(name="read_file", arguments={"path": "m.py"}))
    return runtime


@pytest.mark.asyncio
async def test_successful_patch_returns_numbered_edited_lines(tmp_path):
    runtime = await runtime_with(tmp_path)
    result = await runtime.step(ToolCall(name="patch_file", arguments={
        "path": "m.py", "old_text": "    return 2", "new_text": "    value = 2\n    return value"}))
    assert result.status == "success"
    assert "6:     value = 2\n7:     return value" in result.content
    assert (tmp_path / "m.py").read_text().splitlines()[5:7] == ["    value = 2", "    return value"]


@pytest.mark.asyncio
async def test_unmatched_patch_points_at_closest_current_lines(tmp_path):
    runtime = await runtime_with(tmp_path)
    result = await runtime.step(ToolCall(name="patch_file", arguments={
        "path": "m.py", "old_text": "def b():\n    return 99", "new_text": "x"}))
    assert result.reason == "patch_nonunique"
    assert "Closest current lines:" in result.content and "5: def b():" in result.content
    assert (tmp_path / "m.py").read_text() == SOURCE


@pytest.mark.asyncio
async def test_ambiguous_patch_lists_occurrence_lines(tmp_path):
    runtime = await runtime_with(tmp_path)
    result = await runtime.step(ToolCall(name="patch_file", arguments={
        "path": "m.py", "old_text": "x = 1", "new_text": "y"}))
    assert "Occurrences start at lines 8, 9" in result.content


@pytest.mark.asyncio
async def test_search_context_keeps_offsets_per_match(tmp_path):
    runtime = await runtime_with(tmp_path)
    result = await runtime.step(ToolCall(name="search", arguments={"pattern": "return", "context": 1}))
    assert result.data["count"] == 2
    assert result.content.startswith("m.py:\n1- def a():\n2:     return 1\n3- \n--\n5- def b():")
    plain = await runtime.step(ToolCall(name="search", arguments={"pattern": "return"}))
    assert plain.content == "m.py:\n2:     return 1\n6:     return 2"
    overlap = await runtime.step(ToolCall(name="search", arguments={"pattern": "x = 1", "context": 1}))
    assert overlap.content == "m.py:\n7- \n8: x = 1\n9: x = 1"  # shared context printed once, both matches marked


def test_preview_merges_nearby_regions_and_bounds_size():
    text = "\n".join(f"line {n}" for n in range(1, 201))
    one = text.index("line 10")
    two = text.index("line 12")
    preview = edit_preview(text, [(one, one + 7), (two, two + 7)])
    assert preview.count("--") == 0 and "8: line 8" in preview and "14: line 14" in preview
    far = [(text.index(f"line {n}\n"), text.index(f"line {n}\n") + 5) for n in range(5, 200, 10)]
    assert "preview limited to 80 lines" in edit_preview(text, far)


def test_closest_lines_tolerates_indentation_and_reports_nothing_when_unrelated():
    assert "2:     return 1" in closest_lines(SOURCE, "  return 1")
    assert closest_lines(SOURCE, "completely different text") == ""


def test_archive_pages_are_large_enough_for_one_preview():
    assert TextPage().limit == 4000 and TextPage(limit=12000).limit == 12000


@pytest.mark.asyncio
async def test_one_malformed_read_does_not_cancel_valid_read_only_siblings(tmp_path):
    runtime = await runtime_with(tmp_path)
    results = await runtime._execute_batch([
        ToolCall(name="read_file", arguments={"path": "m.py", "start": 1, "end": 2}),
        ToolCall(name="search", arguments={"path": ".", "regex=false,": "limit"}),
        ToolCall(name="search", arguments={"pattern": "return"}),
    ])
    assert [r.status for r in results] == ["success", "blocked", "success"]
    assert results[1].reason == "invalid_arguments"


@pytest.mark.asyncio
async def test_malformed_call_still_blocks_a_batch_with_valid_writes(tmp_path):
    runtime = await runtime_with(tmp_path)
    results = await runtime._execute_batch([
        ToolCall(name="write_file", arguments={"path": "new.txt", "content": "x"}),
        ToolCall(name="search", arguments={"path": "."}),
    ])
    assert [r.reason for r in results] == ["batch_validation_failed", "invalid_arguments"]
    assert not (tmp_path / "new.txt").exists()


@pytest.mark.asyncio
async def test_whitespace_only_mismatch_is_matched_when_unique(tmp_path):
    runtime = await runtime_with(tmp_path)
    result = await runtime.step(ToolCall(name="patch_file", arguments={
        "path": "m.py", "old_text": "def b():\n  return 2", "new_text": "def b():\n    return 3"}))
    assert result.status == "success" and "ignoring whitespace" in result.content
    assert "def b():\n    return 3\n" in (tmp_path / "m.py").read_text()


@pytest.mark.asyncio
async def test_whitespace_tolerance_never_picks_among_several_matches(tmp_path):
    runtime = await runtime_with(tmp_path)
    result = await runtime.step(ToolCall(name="patch_file", arguments={
        "path": "m.py", "old_text": "  x = 1", "new_text": "y"}))
    assert result.reason == "patch_nonunique"
    assert (tmp_path / "m.py").read_text() == SOURCE


def test_tolerant_span_keeps_line_boundaries():
    from aster.tools.workspace import tolerant_span
    text = "a\n  b  \nc\n"
    start, end = tolerant_span(text, "b\n")
    assert text[start:end] == "  b  \n"
    assert tolerant_span(text, "zzz") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("arguments", [
    {"pattern": "return", "regex=false,": ":"},
    {"pattern": "return", "regex:false,": "garbage}}]"},
    {"pattern": "return", "context": 8},
])
async def test_search_salvages_common_argument_slips(tmp_path, arguments):
    runtime = await runtime_with(tmp_path)
    result = await runtime.step(ToolCall(name="search", arguments=arguments))
    assert result.status == "success" and result.data["count"] == 2


def test_empty_results_say_what_they_mean():
    from aster.context.view import render_output
    def record(**extra):
        return json.dumps({"status": "success", "evidence_id": "e" * 64, "content": "", **extra})

    assert render_output(record(count=0, total=0, offset=0, truncated=False), "list_files") == "[no files]"
    assert render_output(record(count=0, offset=0, truncated=False), "search") == "[no matches]"
    assert render_output(record(count=0), "") == "[no results]"
    assert render_output(record(), "write_file") == "[done]"
    assert render_output(json.dumps({"status": "error", "reason": "x", "content": ""}), "run_shell") == "[error: x]"


def test_chat_transcripts_resolve_tool_names_for_rendering():
    from aster.context.prompt import render_prompt
    record = json.dumps({"status": "success", "evidence_id": "e" * 64, "changed_paths": ["a.py"], "content": ""})
    groups = [[{"role": "assistant", "content": "", "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "write_file", "arguments": "{}"}}]},
        {"type": "function_call_output", "call_id": "c1", "output": record}]]
    prompt = render_prompt("system", "task", [], groups, [], None)
    assert prompt[-1]["output"] == "[done]"  # write_file reports its own change; no [changed: ...] line
