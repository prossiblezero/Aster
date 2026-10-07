"""Selected worker evidence through tool, completion and snapshot boundaries."""

import json

import pytest

from aster.config import Settings
from aster.core.runtime import Runtime
from aster.schema import ModelReply, TaskSpec, ToolCall


@pytest.mark.parametrize("delivery", ["automatic", "single", "batch"])
async def test_model_handoffs_number_sources_without_changing_stored_evidence(tmp_path, delivery):
    (tmp_path / "module.py").write_text("first = 1\n\nanswer = 42\n")
    helper = tmp_path / "helper.py"
    helper.write_text("old = True\n")
    answer = "Finding: " + "x" * 6100 + " End of full answer."

    class Model:
        async def complete(self, messages, _tools):
            reads = [json.loads(item["output"]) for item in messages
                     if item.get("type") == "function_call_output"]
            if not reads:
                return ModelReply(calls=[ToolCall(name="read_file", arguments={"path": path})
                                         for path in ["module.py", "helper.py"]])
            return ModelReply(calls=[ToolCall(name="worker_finish", arguments={
                "answer": answer, "sources": [{"evidence_id": reads[0]["evidence_id"],
                                               "start": 2, "end": 3, "claim": "answer"}],
            })])

    parent = Runtime(tmp_path, Settings(max_workers=1, completion="off"), model=Model())
    parent.start(TaskSpec(prompt="Inspect the answer"))
    worker_id = (await parent.workers.start("Inspect module.py and helper.py"))["worker_id"]
    worker = parent.workers.workers[worker_id]
    await worker.future
    helper.write_text("new = True\n")
    before = await parent.workers.status(worker_id)
    assert before["stale_paths"] == ["helper.py"]
    persisted = worker.runtime.evidence.path("runs", worker.runtime.state.run_id, "handoff.json")
    saved = persisted.read_bytes()
    if delivery == "automatic":
        parent.prepare_request()
        message = next(item for item in parent._structured_history
                       if item.get("content", "").startswith("Worker handoff ("))
        visible = json.loads(message["content"].split("\n", 1)[1])
        assert visible["answer"].startswith(answer[:6000])
        assert "Use worker_status for the full handoff" in visible["answer"]
    outcome = await parent.step(ToolCall(name="worker_status", arguments={
        "worker_id": [worker_id, "missing"] if delivery == "batch" else worker_id,
    }))
    full = json.loads(outcome.content)
    assert full == outcome.data
    raw = full["workers"][0] if delivery == "batch" else full
    assert raw == before
    output = next(json.loads(item["output"]) for item in reversed(parent._structured_history)
                  if item.get("type") == "function_call_output")
    status_view = json.loads(output["content"])
    if delivery == "batch":
        assert status_view["workers"][1] == {"worker_id": "missing", "status": "missing"}
        status_view = status_view["workers"][0]
    assert status_view["answer"] == answer
    for view in ([visible, status_view] if delivery == "automatic" else [status_view]):
        assert "read_evidence" not in view
        assert view["read_paths"] == list(raw["read_evidence"])
        for key in raw.keys() - {"read_evidence", "answer", "source_excerpts"}:
            assert view[key] == raw[key]
        assert view["source_excerpts"] == [{
            "start": 2, "end": 3, "claim": "answer", "path": "module.py",
            "content": "2: \n3: answer = 42", "truncated": False,
        }]
    assert raw["source_excerpts"][0]["content"] == "\nanswer = 42"
    assert len(raw["source_excerpts"][0]["evidence_id"]) == 64
    assert len(raw["source_excerpts"][0]["source_sha256"]) == 64
    assert parent.workers.overview()[0] == before
    assert persisted.read_bytes() == saved
    assert json.loads(saved)["read_evidence"] == before["read_evidence"]
    assert parent.state.observed and not parent.state.read_evidence
    assert parent.state.verification.state == "missing"
    assert not parent.state.pending_handoffs
    await parent.workers.close()


@pytest.mark.parametrize("reference_field", ["evidence_id", "source_ref"])
async def test_worker_selects_earlier_read_ranges_and_finishes_without_extra_request(tmp_path, reference_field):
    lines = [f"value_{index} = {index}" for index in range(1, 101)]
    (tmp_path / "module.py").write_text("\n".join(lines) + "\n")

    class Model:
        def __init__(self):
            self.calls = 0

        async def complete(self, messages, tools):
            self.calls += 1
            assert "worker_finish" in {tool["name"] for tool in tools}
            if self.calls <= 2:
                return ModelReply(calls=[ToolCall(name="read_file", arguments={
                    "path": "module.py", "start": 1 if self.calls == 1 else 90,
                    "end": 80 if self.calls == 1 else 95,
                })])
            assert self.calls == 3, "Delivery must not require a fourth model request"
            result = next(json.loads(item["output"]) for item in messages
                          if item.get("type") == "function_call_output")
            assert result["source_ref"] == result["evidence_id"][:12]
            return ModelReply(calls=[ToolCall(name="worker_finish", arguments={
                "answer": {"first": 40, "second": 70},
                "sources": [{"evidence_id": result[reference_field], "start": line, "end": line,
                             "claim": key} for line, key in [(40, "first"), (70, "second")]],
            })])

    parent = Runtime(tmp_path, Settings(max_workers=1, completion="strict", approval="auto"), model=Model())
    parent.start(TaskSpec(prompt="Investigate two values", require_observation=True))
    started = await parent.workers.start("Inspect module.py and report values 40 and 70")
    worker = parent.workers.workers[started["worker_id"]]
    await worker.future
    assert worker.runtime.state.phase == "completed"
    assert worker.runtime.state.turn == 3 and worker.runtime.state.step == 3
    status = await parent.workers.status(worker.worker_id)
    assert json.loads(status["answer"]) == {"first": 40, "second": 70}
    assert [source["content"] for source in status["source_excerpts"]] == [lines[39], lines[69]]
    assert [source["claim"] for source in status["source_excerpts"]] == ["first", "second"]
    assert all(not source["truncated"] for source in status["source_excerpts"])
    assert all(source["evidence_id"] in worker.runtime.state.worker_reads
               and len(source["evidence_id"]) == 64 for source in status["source_excerpts"])
    assert not parent.state.observed  # UI snapshots do not consume the evidence.
    boundary = parent.session_tree.cursor
    worker.runtime.state.handoff_sources[0]["content"] = "future child mutation"
    restored = Runtime(tmp_path, parent.settings, model=parent.model)
    restored.resume(parent.state.run_id, entry_id=boundary)
    restored.prepare_request()
    assert restored.state.observed and not restored.state.read_evidence
    assert restored.workers.overview()[0]["source_excerpts"][0]["content"] == lines[39]
    assert restored.state.verification.state == "missing" and restored.state.step == 0
    assert restored.finalize("Reviewed both supported findings").phase == "completed"
    restored.continue_task()
    blocked = await restored.step(ToolCall(name="patch_file", arguments={
        "path": "module.py", "old_text": lines[39], "new_text": "value_40 = 0",
    }))
    assert blocked.reason == "fresh_read_required"
    (tmp_path / "module.py").write_text("changed\n")
    assert restored.workers.overview()[0]["stale_paths"] == ["module.py"]
    await parent.workers.close()


def child_runtime(path, **task_options):
    runtime = Runtime(path, Settings(completion="strict", approval="auto"))
    runtime._managed_worker = True
    runtime.start(TaskSpec(prompt="Inspect source", **task_options))
    runtime.state.parent_run_id = "parent"
    return runtime


@pytest.mark.parametrize("case,reason", [
    ("unknown", "handoff_source_unknown"),
    ("outside", "handoff_source_range"),
    ("reversed", "handoff_source_range"),
    ("stale", "handoff_source_stale"),
    ("large", "handoff_source_too_large"),
    ("sibling", "handoff_source_unknown"),
])
@pytest.mark.parametrize("short", [False, True])
async def test_invalid_worker_evidence_cannot_finish_or_install_sources(tmp_path, case, reason, short):
    target = tmp_path / "module.py"
    target.write_text("first\n" + "x" * 8100 + "\nlast\n")
    runtime = child_runtime(tmp_path)
    read = await runtime.step(ToolCall(name="read_file", arguments={"path": "module.py", "end": 2}))
    source = {"evidence_id": read.evidence_id[:12] if short else read.evidence_id,
              "start": 1, "end": 1, "claim": "finding"}
    if case == "unknown":
        source["evidence_id"] = "not-a-read"
    elif case == "outside":
        source["start"] = source["end"] = 3
    elif case == "reversed":
        source["start"] = 2
    elif case == "stale":
        target.write_text("changed\n")
    elif case == "large":
        source["end"] = 2
    elif case == "sibling":
        runtime = child_runtime(tmp_path)
    outcome = await runtime.step(ToolCall(name="worker_finish", arguments={
        "answer": "finding", "sources": [source],
    }))
    assert outcome.status == "blocked" and outcome.reason == reason
    assert not runtime.state.terminal and not runtime.state.handoff_sources
    assert "Source 1" in outcome.content or case == "large"
    if case in {"outside", "reversed"}:
        assert "module.py" in outcome.content and "available lines 1-2" in outcome.content
        assert f"requested {source['start']}-{source['end']}" in outcome.content
    elif case == "large":
        assert "Selected 8106" in outcome.content and "limit 8000" in outcome.content
        assert "1=8106" in outcome.content
    elif case in {"unknown", "sibling"}:
        assert "source_ref (12 characters)" in outcome.content


@pytest.mark.parametrize("invalid", ["shorter", "longer", "typo", "missing"])
async def test_worker_references_never_fuzzy_correct_ids(tmp_path, invalid):
    (tmp_path / "module.py").write_text("answer = 42\n")
    child = child_runtime(tmp_path)
    read = await child.step(ToolCall(name="read_file", arguments={"path": "module.py"}))
    other = read.evidence_id[:-1] + ("1" if read.evidence_id[-1] == "0" else "0")
    reference = {"shorter": read.evidence_id[:11], "longer": read.evidence_id[:13],
                 "typo": other, "missing": ("1" if read.evidence_id[0] == "0" else "0") + read.evidence_id[1:12]}[invalid]
    result = await child.step(ToolCall(name="worker_finish", arguments={
        "answer": "finding", "sources": [{"evidence_id": reference, "start": 1,
                                           "end": 1, "claim": "answer"}],
    }))
    assert result.reason == "handoff_source_unknown"
    assert not child.state.terminal and not child.state.handoff_sources


async def test_ambiguous_short_reference_requires_exact_full_id(tmp_path):
    (tmp_path / "module.py").write_text("answer = 42\n")
    child = child_runtime(tmp_path)
    read = await child.step(ToolCall(name="read_file", arguments={"path": "module.py"}))
    # Force a prefix collision without attempting to collide SHA-256 observations.
    collision = read.evidence_id[:-1] + ("1" if read.evidence_id[-1] == "0" else "0")
    child.state.worker_reads[collision] = child.state.run_id
    selection = {"evidence_id": read.evidence_id[:12], "start": 1, "end": 1, "claim": "answer"}
    outcome = await child.step(ToolCall(name="worker_finish", arguments={"answer": "42", "sources": [selection]}))
    assert outcome.reason == "handoff_source_ambiguous" and "full evidence_id" in outcome.content
    assert not child.state.terminal and not child.state.handoff_sources
    selection["evidence_id"] = collision
    missing = await child.step(ToolCall(name="worker_finish", arguments={"answer": "42", "sources": [selection]}))
    assert missing.reason == "handoff_source_unread"
    assert "observation unavailable" in missing.content
    selection["evidence_id"] = read.evidence_id
    result = await child.step(ToolCall(name="worker_finish", arguments={"answer": "42", "sources": [selection]}))
    assert result.status == "success"
    assert child.state.handoff_sources[0]["evidence_id"] == read.evidence_id


async def test_source_size_feedback_supports_one_precise_repair(tmp_path):
    (tmp_path / "module.py").write_text("\u4e2d" * 4000 + "\n" + "x" * 4001 + "\ny\n")
    child = child_runtime(tmp_path)
    read = await child.step(ToolCall(name="read_file", arguments={"path": "module.py"}))
    selections = [{"evidence_id": read.evidence_id[:12], "start": n, "end": n, "claim": "size"}
                  for n in (1, 2)]
    result = await child.step(ToolCall(name="worker_finish", arguments={"answer": "finding", "sources": selections}))
    assert result.reason == "handoff_source_too_large"
    assert "Selected 8001" in result.content and "1=4000, 2=4001" in result.content
    selections[1].update(start=1, end=1)  # Exactly 8000 characters across both selections.
    result = await child.step(ToolCall(name="worker_finish", arguments={"answer": "finding", "sources": selections}))
    assert result.status == "success" and child.state.phase == "completed"
    assert all(source["evidence_id"] == read.evidence_id for source in child.state.handoff_sources)


async def test_worker_delivers_complete_support_over_four_thousand_characters_once(tmp_path):
    source = "\n".join(f"value_{n:03} = {n}  # a supporting source observation" for n in range(120))
    assert 4000 < len(source) < 8000
    (tmp_path / "module.py").write_text(source)
    child = child_runtime(tmp_path)
    read = await child.step(ToolCall(name="read_file", arguments={"path": "module.py"}))
    outcome = await child.step(ToolCall(name="worker_finish", arguments={
        "answer": {"last_value": 119},
        "sources": [{"evidence_id": read.evidence_id[:12], "start": 1, "end": 120, "claim": "values"}],
    }))
    assert outcome.status == "success" and child.state.phase == "completed"
    assert json.loads(child.state.final) == {"last_value": 119}
    assert child.state.handoff_sources[0]["content"] == source
    assert child.state.handoff_sources[0]["end"] == 120
    assert child.state.handoff_sources[0]["truncated"] is False


@pytest.mark.parametrize("requirement", ["require_edit", "require_verification"])
async def test_worker_finish_keeps_completion_and_parent_capability_boundaries(tmp_path, requirement):
    parent = Runtime(tmp_path, Settings(max_workers=2, tool_profile="all"))
    parent.start(TaskSpec(prompt="Inspect"))
    assert "worker_finish" not in {tool["name"] for tool in parent.tool_definitions()}
    outcome = await parent.step(ToolCall(name="worker_finish", arguments={"answer": "done"}))
    assert outcome.reason == "worker_only" and not parent.state.terminal
    child = child_runtime(tmp_path, require_observation=False, **{requirement: True})
    outcome = await child.step(ToolCall(name="worker_finish", arguments={"answer": "No edits"}))
    assert outcome.status == "blocked" and child.state.phase != "completed"
    assert not child.state.handoff_sources


@pytest.mark.parametrize("short", [False, True])
async def test_selected_sources_survive_ancestor_continuation_but_reject_future_branch(tmp_path, short):
    (tmp_path / "module.py").write_text("old observation\nnew observation\n")
    child = child_runtime(tmp_path)
    first = await child.step(ToolCall(name="read_file", arguments={"path": "module.py", "end": 1}))
    child.prepare_request()  # Freeze the ancestor observation allowlist.
    child.checkpoint()
    boundary = child.session_tree.cursor
    future_call = ToolCall(name="read_file", call_id="future-branch-call", arguments={
        "path": "module.py", "start": 2, "end": 2,
    })
    future = await child.step(future_call)
    restored = child_runtime(tmp_path)
    restored.finish("switch")
    restored.resume(child.state.run_id, entry_id=boundary)
    restored.continue_task("Explain the original observation")
    await restored.step(ToolCall(name="list_files", call_id=future_call.call_id))
    for evidence_id, line, expected in [(future.evidence_id, 2, "blocked"), (first.evidence_id, 1, "success")]:
        result = await restored.step(ToolCall(name="worker_finish", arguments={
            "answer": "Old finding", "sources": [{"evidence_id": evidence_id[:12] if short else evidence_id, "start": line,
                                                   "end": line, "claim": "original finding"}],
        }))
        assert result.status == expected
    assert restored.state.phase == "completed"
    assert restored.state.handoff_sources[0]["content"] == "old observation"
    assert not restored.state.read_evidence
    restored.continue_task("Another question")
    assert not restored.state.handoff_sources


async def test_selected_sources_are_redacted_before_delivery(tmp_path):
    secret = "sk-examplecredential1234567890"
    (tmp_path / "module.py").write_text(f'token = "{secret}"\nanswer = 42\n')
    child = child_runtime(tmp_path)
    read = await child.step(ToolCall(name="read_file", arguments={"path": "module.py"}))
    result = await child.step(ToolCall(name="worker_finish", arguments={
        "answer": "answer = 42", "sources": [{"evidence_id": read.evidence_id, "start": 1,
                                               "end": 2, "claim": "source context"}],
    }))
    assert result.status == "success"
    assert secret not in json.dumps(child.state.handoff_sources)
    assert "[REDACTED]" in child.state.handoff_sources[0]["content"]
