"""Real Runtime/SQLite transitions, including crash boundaries and sibling isolation."""
import asyncio
import json
import shutil

import pytest
from pydantic import SecretStr

from aster.config import ModelProfile, Settings
from aster.core.runtime import Runtime
from aster.schema import ModelReply, TaskSpec, ToolCall
from aster.sessions.tree import SessionTree


class Echo:
    async def complete(self, messages, tools):
        return ModelReply(text="已记录这个分支的结论")


def make(root, **kwargs):
    return Runtime(root, Settings(completion="off", approval="auto", **kwargs), model=Echo())


async def completed(runtime, prompt):
    if runtime.state:
        runtime.continue_task(prompt)
        await runtime.run()
    else:
        await runtime.run(TaskSpec(prompt=prompt))
    return runtime.session_tree.cursor


async def test_branch_restores_ancestor_without_sibling_history(tmp_path):
    r = make(tmp_path)
    ancestor = await completed(r, "shared request")
    await completed(r, "A_ONLY retain duplicates")
    a = r.session_tree.cursor
    r.checkout(ancestor)
    await completed(r, "B_ONLY remove duplicates")
    prompt, _, _ = r.prepare_request()
    assert "B_ONLY" in json.dumps(prompt) and "A_ONLY" not in json.dumps(prompt)
    assert "A_ONLY" in json.dumps(r.session_tree.restore(a))
    fresh = make(tmp_path)
    fresh.checkout(a[:12])
    prompt, _, _ = fresh.prepare_request()
    assert "A_ONLY" in json.dumps(prompt) and "B_ONLY" not in json.dumps(prompt)
    assert fresh.state.run_id != r.state.run_id


async def test_summary_has_source_and_requires_explicit_choice(tmp_path):
    r = make(tmp_path)
    root = await completed(r, "shared")
    await completed(r, "A_ONLY discovered limitation")
    source = r.session_tree.cursor
    r.checkout(root, summary=True)
    prompt, _, _ = r.prepare_request()
    assert "A_ONLY" in json.dumps(prompt)
    summaries = [e for e in r.session_tree.entries(r.state.session_id) if e["kind"] == "branch_summary"]
    assert summaries[0]["payload"]["source_entry"] == source
    assert summaries[0]["parent_id"] != source
    assert not r.memory.state["lessons"]


async def test_compaction_is_immutable_and_branch_local(tmp_path):
    r = make(tmp_path)
    start = await completed(r, "original")
    original = r.session_tree.restore(start)["structured_history"]
    r._structured_history[:] = [{"role": "user", "content": "COMPACTED summary"}]
    r.checkpoint()
    compact = r.session_tree.cursor
    assert any(e["kind"] == "compaction" for e in r.session_tree.entries(r.state.session_id))
    r.checkout(start)
    assert r._structured_history == original
    r.checkout(compact)
    assert "COMPACTED" in json.dumps(r._structured_history)
    assert r.session_tree.restore(start)["structured_history"] == original


async def test_model_profile_and_default_restore(tmp_path):
    profile = ModelProfile(provider="local", model="other", protocol="chat", base_url="http://localhost:9/v1", auth="none", context_tokens=20000, output_tokens=512)
    r = make(tmp_path, profiles={"other": profile})
    default = await completed(r, "initial model")
    r.switch_model("other")
    other = r.session_tree.cursor
    r.checkout(default)
    assert r.settings.model == "gpt-5.6-luna" and r.settings.profile is None
    r.checkout(other)
    assert r.settings.profile == "other" and r.settings.protocol == "chat"
    assert r.state.session_id == r.session_tree.restore(default)["state"]["session_id"]


async def test_workspace_adoption_is_explicit_and_does_not_rewind_files(tmp_path):
    r = make(tmp_path)
    entry = await completed(r, "before files")
    (tmp_path / "user.txt").write_text("keep external edit")
    state, cursor = r.state, r.session_tree.cursor
    with pytest.raises(ValueError, match="workspace_mismatch"):
        r.checkout(entry)
    assert r.state is state and r.session_tree.cursor == cursor
    r.checkout(entry, adopt_workspace=True)
    assert (tmp_path / "user.txt").read_text() == "keep external edit"
    assert "重新读取" in json.dumps(r._structured_history, ensure_ascii=False)


async def test_past_boundary_cannot_read_future_observations(tmp_path):
    r = make(tmp_path)
    r.start(TaskSpec(prompt="read evidence"))
    early = r.evidence.archive_observation(r.state.run_id, "early", "read_file", "ancestor fact")
    r.checkpoint()
    entry = r.session_tree.cursor
    late = r.evidence.archive_observation(r.state.run_id, "late", "read_file", "SIBLING_SECRET")
    r.checkpoint()
    r.checkout(entry)
    assert early["id"] in r._archive_sources and late["id"] not in r._archive_sources
    from aster.sessions.lifecycle import read_session_observation
    args = dict(offset=0, limit=1000, query="", observation_id=late["id"])
    outcome = read_session_observation(r.evidence, r.state.run_id, r._archive_sources, args)
    assert outcome.status == "blocked" and "SIBLING_SECRET" not in outcome.content


async def test_uncertain_tool_effect_is_never_replayed_by_checkout(tmp_path):
    r = make(tmp_path)
    r.start(TaskSpec(prompt="write a file"))
    entry = r.session_tree.cursor
    r._mark_inflight([ToolCall(name="write_file", call_id="pending", arguments={"path": "x", "content": "x"})])
    (tmp_path / "x").write_text("side effect already happened")
    with pytest.raises(ValueError, match="interrupted_tool_batch"):
        r.checkout(entry, adopt_workspace=True)
    assert (tmp_path / "x").read_text() == "side effect already happened"


async def test_portable_checkpoint_import_preserves_old_file(tmp_path):
    r = make(tmp_path / "old")
    await completed(r, "portable history")
    target = tmp_path / "new"
    target.mkdir()
    shutil.copytree(r.evidence.path("runs"), target / ".aster/runs")
    restored = make(target)
    checkpoint = restored.evidence.path("runs", r.state.run_id, "checkpoint.json")
    before = checkpoint.read_bytes()
    restored.resume(r.state.run_id)
    assert checkpoint.read_bytes() == before
    await completed(restored, "followup")
    assert any(e["kind"] == "legacy_import" for e in restored.session_tree.entries(restored.state.session_id))


async def test_snapshot_transaction_survives_json_export_failure(tmp_path, monkeypatch):
    r = make(tmp_path)
    await completed(r, "old")
    original = r.evidence.save
    def fail(parts, data, **kwargs):
        if parts[-1] == "checkpoint.json":
            raise OSError("simulated disk failure after SQLite commit")
        return original(parts, data, **kwargs)
    monkeypatch.setattr(r.evidence, "save", fail)
    with pytest.raises(OSError):
        r.continue_task("new durable request")
    head = r.session_tree.session_head(r.state.session_id)
    restored = make(tmp_path)
    restored.checkout(head)
    assert "new durable request" in json.dumps(restored._structured_history)


async def test_conflicting_session_writer_does_not_overwrite_head(tmp_path):
    r = make(tmp_path)
    entry = await completed(r, "first")
    stale = SessionTree(r.evidence)
    data = r.session_tree.restore(entry)
    stale.attach(data)
    await completed(r, "second")
    with pytest.raises(RuntimeError, match="session_changed"):
        stale.commit(data)
    assert r.session_tree.session_head(r.state.session_id) == r.session_tree.cursor


async def test_concurrent_runs_require_distinct_workspaces(tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()
    class Wait:
        async def complete(self, *_):
            entered.set()
            await release.wait()
            return ModelReply(text="done")
    first, other = make(tmp_path), make(tmp_path)
    first.model = Wait()
    running = asyncio.create_task(first.run(TaskSpec(prompt="hold workspace")))
    await entered.wait()
    with pytest.raises(RuntimeError, match="workspace_busy"):
        await other.run(TaskSpec(prompt="must not overlap"))
    # A persisted parent ID alone does not grant a restored session worker privileges.
    other.state.parent_run_id = first.state.run_id
    with pytest.raises(RuntimeError, match="workspace_busy"):
        await other.run()
    independent = make(tmp_path / "separate")
    await completed(independent, "independent workspace")
    release.set()
    await running
    assert independent.state.terminal


async def test_all_persisted_session_text_is_redacted(tmp_path):
    secret = "test-super-secret-session-credential"
    r = make(tmp_path, api_key=SecretStr(secret))
    await completed(r, "request " + secret)
    entries = r.session_tree.entries(r.state.session_id)
    assert secret not in json.dumps(entries)
    assert secret not in json.dumps(r.session_tree.sessions())
    assert secret not in json.dumps(r.session_tree.restore(r.session_tree.cursor))


async def test_selection_preserves_transcript_without_double_counting_usage(tmp_path):
    r = make(tmp_path)
    r.start(TaskSpec(prompt="request"))
    r.state.usage = {"total_tokens": 345}
    r.state.turn = 3
    r.state.step = 2
    r.finish("done")
    old = r.session_tree.cursor
    r.checkout(old)
    assert r.state.usage == {} and r.state.turn == r.state.step == 0
    assert r.session_tree.restore(old)["state"]["usage"] == {"total_tokens": 345}


async def test_idle_sessions_refresh_shared_memory_before_next_operation(tmp_path):
    text_a, text_b = "Service port is 8080.", "Build output is public."
    (tmp_path / "PROJECT.md").write_text(text_a + "\n" + text_b)
    first, second = make(tmp_path, memory_enabled=True), make(tmp_path, memory_enabled=True)
    for r, text in [(first, text_a), (second, text_b)]:
        r.start(TaskSpec(prompt="read project conventions"))
        read = await r.step(ToolCall(name="read_file", arguments={"path": "PROJECT.md"}))
        outcome = await r.step(ToolCall(name="memory_store", arguments={"text": text, "evidence_id": read.evidence_id}))
        assert outcome.status == "success", outcome
    assert {row["text"] for row in second.memory.state["lessons"]} == {text_a, text_b}
    await first.step(ToolCall(name="list_files"))
    assert {row["text"] for row in first.memory.state["lessons"]} == {text_a, text_b}
