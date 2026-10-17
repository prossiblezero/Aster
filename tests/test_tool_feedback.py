"""Tool results answer the model's next question so it does not spend a turn asking it."""

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
        "path": "m.py", "old_text": "def b():\n  return 2", "new_text": "x"}))
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
    assert "m.py-1-def a():\nm.py:2:    return 1\nm.py-3-" in result.content
    plain = await runtime.step(ToolCall(name="search", arguments={"pattern": "return"}))
    assert plain.content == "m.py:2:    return 1\nm.py:6:    return 2"


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
