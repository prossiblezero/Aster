"""Excerpt admission authenticates Runtime evidence before modifying stored lessons."""

from copy import deepcopy

import pytest

from aster.config import Settings
from aster.memory import store_excerpt
from aster.runtime import Runtime
from aster.schema import TaskSpec, ToolCall


def runtime_for(root):
    runtime = Runtime(root, Settings(memory_enabled=True, completion="off"))
    runtime.start(TaskSpec(prompt="Inspect project conventions"))
    return runtime


async def test_excerpt_admission_and_replacement_keep_authenticated_provenance(tmp_path):
    source = tmp_path / "PROJECT.md"
    source.write_text("Service port is 8080.\nBuild output is public.\n")
    runtime = runtime_for(tmp_path)
    read = await runtime.step(ToolCall(name="read_file", arguments={"path": source.name}))
    stored = store_excerpt(
        runtime.memory,
        runtime.evidence,
        runtime.state.run_id,
        text="Service port is 8080.",
        evidence_id=read.evidence_id,
        tags=["port"],
    )
    assert stored.status == "success"
    assert stored.data["provenance"]["evidence_id"] == read.evidence_id
    assert stored.data["sources"] == {source.name: read.data["source_sha256"]}
    read_event = next(
        row
        for row in runtime.evidence.events(runtime.state.run_id)
        if row["event"] == "tool_result"
    )
    replacement = store_excerpt(
        runtime.memory,
        runtime.evidence,
        runtime.state.run_id,
        text="Build output is public.",
        evidence_id=read_event["hash"],
        lesson_id=stored.data["id"],
    )
    assert replacement.status == "success"
    old = next(item for item in runtime.memory.state["lessons"] if item["id"] == stored.data["id"])
    assert old["inactive_reason"] == "superseded"
    assert [item["id"] for item in runtime.memory.retrieve("Build output")] == [
        replacement.data["id"]
    ]


@pytest.mark.parametrize("invalid", ["other_run", "other_tool", "failed_read", "missing_source"])
async def test_excerpt_admission_rejects_invalid_evidence_before_store(tmp_path, invalid):
    (tmp_path / "PROJECT.md").write_text("Service port is 8080.")
    runtime = runtime_for(tmp_path)
    call = ToolCall(name="read_file", arguments={"path": "PROJECT.md"})
    if invalid == "other_tool":
        call = ToolCall(name="list_files")
    elif invalid == "failed_read":
        call.arguments["path"] = "missing.md"
    read = await runtime.step(call)
    reference = read.evidence_id
    if invalid == "other_run":
        runtime.finish("cancelled")
        runtime.start(TaskSpec(prompt="New independent task"))
    elif invalid == "missing_source":
        reference = runtime.evidence.emit(
            runtime.state.run_id,
            "tool_result",
            call={"name": "read_file"},
            outcome={"status": "success", "content": "Service port is 8080.", "data": {}},
        )["hash"]
    before = deepcopy(runtime.memory.state)
    result = store_excerpt(
        runtime.memory,
        runtime.evidence,
        runtime.state.run_id,
        text="Service port is 8080.",
        evidence_id=reference,
    )
    assert result.reason == "memory_provenance"
    assert runtime.memory.state == before


@pytest.mark.parametrize(
    "invalid,reason",
    [
        ("stale_source", "stale_source"),
        ("summary", "unsupported_excerpt"),
        ("missing_lesson", "memory_not_found"),
    ],
)
async def test_rejected_update_keeps_original_lesson(tmp_path, invalid, reason):
    source = tmp_path / "PROJECT.md"
    source.write_text("Service port is 8080.")
    runtime = runtime_for(tmp_path)
    read = await runtime.step(ToolCall(name="read_file", arguments={"path": source.name}))
    stored = store_excerpt(
        runtime.memory,
        runtime.evidence,
        runtime.state.run_id,
        text="Service port is 8080.",
        evidence_id=read.evidence_id,
    )
    before = deepcopy(runtime.memory.state["lessons"])
    if invalid == "stale_source":
        source.write_text("Service port is 9090.")
    result = store_excerpt(
        runtime.memory,
        runtime.evidence,
        runtime.state.run_id,
        text="The server uses a standard port."
        if invalid == "summary"
        else "Service port is 8080.",
        evidence_id=read.evidence_id,
        lesson_id="missing" if invalid == "missing_lesson" else stored.data["id"],
    )
    assert result.reason == reason and result.status == "blocked"
    assert runtime.memory.state["lessons"] == before
