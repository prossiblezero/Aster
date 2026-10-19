import json

import pytest

from aster.config import Settings
from aster.core.runtime import Runtime
from aster.providers.codec import request_payload
from aster.schema import TaskSpec, ToolCall


async def test_search_pages_all_1200_matches_in_one_file(tmp_path):
    (tmp_path / "matches.txt").write_text("".join(f"needle {number}\n" for number in range(1200)))
    runtime = Runtime(tmp_path, Settings())
    runtime.start(TaskSpec(prompt="inspect"))
    found, offset = [], 0
    for expected_count, next_offset in [(500, 500), (500, 1000), (200, None)]:
        result = await runtime.step(
            ToolCall(name="search", arguments={"pattern": "needle", "offset": offset})
        )
        assert result.status == "success"
        content = result.content
        if "observation" in result.data:
            # The page keeps its original record cursor; recover its archived body
            # before advancing, as the search tool contract requires for previews.
            chunks, character_offset = [], 0
            while character_offset is not None:
                recovered = await runtime.workflow_tool("observation_read", {
                    "observation_id": result.data["observation"]["id"],
                    "offset": character_offset, "limit": 1000, "query": "",
                })
                assert recovered.status == "success"
                page = json.loads(recovered.content)
                chunks.append(page["content"])
                character_offset = page["next_offset"]
            content = "".join(chunks)
            assert result.content.startswith(content[:8192]) and "observation_read" in result.content
        lines = content.splitlines()
        assert lines[0] == "matches.txt:"  # one heading per file
        lines = lines[1:]
        assert len(lines) == expected_count
        assert {key: value for key, value in result.data.items() if key != "observation"} == {
            "offset": offset,
            "count": expected_count,
            "truncated": next_offset is not None,
            "next_offset": next_offset,
        }
        found.extend(lines)
        offset = next_offset
    assert found == [f"{number + 1}: needle {number}" for number in range(1200)]
    assert runtime.state.read_evidence == {}


async def test_list_files_pages_past_500_without_losing_files(tmp_path):
    expected = [f"file-{number:04d}.txt" for number in range(503)]
    for name in expected:
        (tmp_path / name).touch()
    runtime = Runtime(tmp_path, Settings())
    runtime.start(TaskSpec(prompt="inspect"))
    first = await runtime.step(ToolCall(name="list_files"))
    assert first.status == "success"
    assert first.data == {
        "offset": 0,
        "count": 500,
        "truncated": True,
        "next_offset": 500,
        "total": 503,
    }
    last = await runtime.step(
        ToolCall(name="list_files", arguments={"offset": first.data["next_offset"]})
    )
    assert last.status == "success"
    assert last.data == {
        "offset": 500,
        "count": 3,
        "truncated": False,
        "next_offset": None,
        "total": 503,
    }
    assert first.content.splitlines() + last.content.splitlines() == expected


async def test_search_keeps_literal_matching_scope_and_bounded_line_previews(tmp_path):
    (tmp_path / "source").mkdir()
    (tmp_path / "source/a.txt").write_text("A|B " + "x" * 4000 + "\na only\na|b last\n")
    (tmp_path / "other.txt").write_text("a|b outside requested path\n")
    (tmp_path / "source/.env").write_text("a|b protected\n")
    (tmp_path / "source/linked.txt").symlink_to(tmp_path / "other.txt")
    runtime = Runtime(tmp_path, Settings())
    runtime.start(TaskSpec(prompt="inspect"))
    first = await runtime.step(
        ToolCall(name="search", arguments={"pattern": "a|b", "path": "source", "limit": 1})
    )
    assert first.status == "success"
    assert first.content == "source/a.txt:\n1: " + ("A|B " + "x" * 4000)[:300]
    assert first.data["count"] == 1 and first.data["next_offset"] == 1
    last = await runtime.step(
        ToolCall(name="search", arguments={"pattern": "a|b", "path": "source", "offset": 1, "limit": 1})
    )
    assert last.content == "source/a.txt:\n3: a|b last"
    assert last.data["count"] == 1 and not last.data["truncated"]
    assert last.data["next_offset"] is None


@pytest.mark.parametrize("name", ["list_files", "search"])
@pytest.mark.parametrize("page", [{"offset": -1}, {"limit": 0}, {"limit": 501}])
async def test_tool_page_bounds_reject_invalid_arguments(tmp_path, name, page):
    runtime = Runtime(tmp_path, Settings())
    runtime.start(TaskSpec(prompt="inspect"))
    arguments = {"pattern": "needle"} if name == "search" else {}
    result = await runtime.step(ToolCall(name=name, arguments={**arguments, **page}))
    assert (result.status, result.reason) == ("blocked", "invalid_arguments")


@pytest.mark.parametrize("name", ["list_files", "search"])
async def test_tool_page_metadata_reaches_model_request(tmp_path, name):
    for number in range(3):
        (tmp_path / f"{number}.txt").write_text("needle\n")
    runtime = Runtime(tmp_path, Settings())
    runtime.start(TaskSpec(prompt="inspect"))
    arguments = {"pattern": "needle"} if name == "search" else {}
    result = await runtime.step(ToolCall(name=name, arguments={**arguments, "limit": 2}))
    assert result.status == "success"
    messages, tools, _ = runtime.prepare_request()
    payload = request_payload(messages, tools, protocol="responses", model="test", output_tokens=2048)
    record = next(item for item in payload["input"] if item.get("type") == "function_call_output")
    output = json.loads(record["output"])
    assert output["content"] == result.content
    for field in ["offset", "count", "truncated", "next_offset"]:
        assert output[field] == result.data[field]
    assert output["next_offset"] == 2 and output["truncated"]
    if name == "list_files":
        assert output["total"] == 3


@pytest.mark.parametrize("name", ["list_files", "search"])
async def test_offset_past_end_returns_empty_terminal_page(tmp_path, name):
    (tmp_path / "one.txt").write_text("needle\n")
    runtime = Runtime(tmp_path, Settings())
    runtime.start(TaskSpec(prompt="inspect"))
    arguments = {"pattern": "needle"} if name == "search" else {}
    result = await runtime.step(ToolCall(name=name, arguments={**arguments, "offset": 5}))
    assert result.status == "success" and result.content == ""
    assert result.data["count"] == 0 and not result.data["truncated"]
    assert result.data["next_offset"] is None


async def test_regex_search_alternation_literal_default_and_limits(tmp_path):
    (tmp_path / "names.txt").write_text("FOO\nbar\nfoo|bar\n" + "x" * 2000 + "foo\n")
    runtime = Runtime(tmp_path, Settings())
    runtime.start(TaskSpec(prompt="inspect"))
    regex = await runtime.step(ToolCall(name="search", arguments={
        "pattern": "foo|bar", "regex": True,
    }))
    assert regex.status == "success"
    assert regex.content.splitlines() == ["names.txt:", "1: FOO", "2: bar", "3: foo|bar"]
    for option in ({}, {"regex": False}):
        literal = await runtime.step(ToolCall(name="search", arguments={
            "pattern": "foo|bar", **option,
        }))
        assert literal.content == "names.txt:\n3: foo|bar"
    beyond = await runtime.step(ToolCall(name="search", arguments={"pattern": "foo"}))
    assert beyond.data["count"] == 3  # Literal search still examines the full line.
    invalid = await runtime.step(ToolCall(name="search", arguments={
        "pattern": "[", "regex": True,
    }))
    assert (invalid.status, invalid.reason) == ("blocked", "invalid_regex")
    assert invalid.content
    oversized = await runtime.step(ToolCall(name="search", arguments={"pattern": "x" * 301}))
    assert oversized.reason == "invalid_arguments"
