"""Tool outputs resent on every later request carry facts, not bookkeeping."""

import json

import pytest

from aster.config import Settings
from aster.core.runtime import Runtime
from aster.schema import TaskSpec, ToolCall

pytestmark = pytest.mark.core


def last_output(runtime):
    item = next(x for x in reversed(runtime._structured_history) if x.get("type") == "function_call_output")
    return json.loads(item["output"])


@pytest.mark.asyncio
async def test_successful_read_omits_empty_fields_and_full_hashes(tmp_path):
    (tmp_path / "a.txt").write_text("alpha\n")
    runtime = Runtime(tmp_path, Settings(approval="auto", memory_enabled=False))
    runtime.start(TaskSpec(prompt="inspect a.txt"))
    runtime._in_turn = False
    result = await runtime.step(ToolCall(name="read_file", arguments={"path": "a.txt"}))
    payload = last_output(runtime)
    assert payload == {"status": "success", "evidence_id": result.evidence_id[:12],
                       "content": "1: alpha", "path": "a.txt", "start": 1, "end": 1}


@pytest.mark.asyncio
async def test_blocked_and_edit_outputs_keep_reason_and_changed_paths(tmp_path):
    (tmp_path / "a.txt").write_text("alpha\n")
    runtime = Runtime(tmp_path, Settings(approval="auto", memory_enabled=False))
    runtime.start(TaskSpec(prompt="edit a.txt"))
    await runtime.step(ToolCall(name="patch_file", arguments={"path": "a.txt", "old_text": "alpha", "new_text": "beta"}))
    assert last_output(runtime)["reason"] == "fresh_read_required"
    await runtime.step(ToolCall(name="read_file", arguments={"path": "a.txt"}))
    await runtime.step(ToolCall(name="patch_file", arguments={"path": "a.txt", "old_text": "alpha", "new_text": "beta"}))
    assert last_output(runtime)["changed_paths"] == ["a.txt"]


@pytest.mark.asyncio
async def test_memory_store_accepts_the_visible_evidence_prefix(tmp_path):
    (tmp_path / "settings.py").write_text("RETRIES = 4\n")
    runtime = Runtime(tmp_path, Settings(approval="auto", completion="off", memory_enabled=True,
                                         memory_evolution=True))
    runtime.start(TaskSpec(prompt="Inspect retry settings"))
    read = await runtime.step(ToolCall(name="read_file", arguments={"path": "settings.py"}))
    saved = await runtime.step(ToolCall(name="memory_store", arguments={
        "text": "Retry at most four times.", "kind": "fact", "when": "Configuring retries",
        "evidence_id": read.evidence_id[:12]}))
    assert saved.status == "success", saved
    assert saved.data["provenance"]["evidence_id"] == read.evidence_id
    too_short = await runtime.step(ToolCall(name="memory_store", arguments={
        "text": "Retry at most four times.", "kind": "fact", "when": "Configuring retries",
        "evidence_id": read.evidence_id[:8]}))
    assert too_short.reason == "memory_provenance"
