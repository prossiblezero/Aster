"""Compression preserves legal exchanges and makes omitted evidence safely recoverable."""

import json
from copy import deepcopy

import pytest

from aster.config import Settings
from aster.core.runtime import Runtime
from aster.messages import history_groups
from aster.providers.codec import request_payload
from aster.schema import TaskSpec, ToolCall
from aster.storage.workspace import digest


def runtime_at(root, **settings):
    runtime = Runtime(
        root,
        Settings(
            approval="auto",
            memory_enabled=False,
            context_tokens=4096,
            output_tokens=512,
            **settings,
        ),
    )
    runtime.start(TaskSpec(prompt="Find the cause using the evidence and report it."))
    return runtime


def exchange(call_id, content, *, tool="run_shell", arguments=None):
    return [
        {
            "type": "function_call",
            "call_id": call_id,
            "name": tool,
            "arguments": json.dumps(arguments or {"command": "cat diagnostic.txt"}),
        },
        {
            "type": "function_call_output",
            "call_id": call_id,
            "output": json.dumps(
                {
                    "status": "success",
                    "reason": "",
                    "content": content,
                    "evidence_id": f"evidence-{call_id}",
                    "changed_paths": [],
                }
            ),
        },
    ]


@pytest.mark.parametrize("protocol", ["responses", "chat", "anthropic"])
def test_latest_parallel_batch_fits_one_budget_with_all_results(tmp_path, protocol):
    runtime = runtime_at(tmp_path, protocol=protocol)
    exchanges = [
        exchange(f"call-{i}", f"log-{i} " * 4000 + f"final diagnosis {i}") for i in range(4)
    ]
    runtime._structured_history = [items[0] for items in exchanges] + [
        items[1] for items in reversed(exchanges)
    ]
    calls = deepcopy(runtime._structured_history[:4])
    prompt, tools, report = runtime.prepare_request()
    payload = request_payload(
        prompt, tools, protocol=protocol, model=runtime.settings.model, output_tokens=512,
        prompt_cache_key=runtime.state.session_id,
    )
    assert runtime.context.count(payload) == report.estimated_tokens <= 3584
    assert report.compacted and report.removed_messages == 0
    assert runtime._structured_history[:4] == calls
    from aster.context.prompt import is_context_note

    groups = history_groups(runtime._structured_history)
    assert len(groups) == 2 and len(groups[-1]) == 1 and is_context_note(groups[-1][0])
    outputs = groups[0][4:]
    assert {item["call_id"] for item in outputs} == {f"call-{i}" for i in range(4)}
    assert "observation_read" not in {tool["name"] for tool in tools}
    catalog = next(tool for tool in tools if tool["name"] == "tool_load")
    assert "observation_read" in catalog["parameters"]["properties"]["names"]["items"]["enum"]
    for item in outputs:
        result = json.loads(item["output"])
        assert result["status"] == "success"
        assert result["evidence_id"] == f"evidence-{item['call_id']}"
        archived = runtime.evidence.read_observation(
            runtime.state.run_id, result["observation"]["id"], query="final diagnosis"
        )
        assert archived["call_id"] == item["call_id"]
        assert archived["matched"] and archived["content"].startswith("final diagnosis")
        assert archived["id"] == result["observation"]["id"]
        assert archived["total"] == result["observation"]["characters"]


async def test_removed_one_time_evidence_is_searchable_through_the_model_tool(tmp_path):
    runtime = runtime_at(tmp_path)
    witness = "one-time nonce: 719-ONYX; do not rerun the destructive probe"
    source = "diagnostic prefix " * 600 + witness + " diagnostic suffix" * 600
    runtime._structured_history = exchange("one-time", source)
    for i in range(18):
        runtime._structured_history.extend(exchange(f"later-{i}", "routine log " * 600))
    prompt, _tools, report = runtime.prepare_request()
    assert report.removed_messages > 0
    assert not any(item.get("call_id") == "one-time" for item in runtime._structured_history)
    assert witness not in str(prompt)
    found = await runtime.step(ToolCall(name="observation_read", arguments={"query": "719-ONYX"}))
    assert found.status == "success", found
    matches = json.loads(found.content)["observations"]
    assert len(matches) == 1 and matches[0]["call_id"] == "one-time"
    fetched = await runtime.step(
        ToolCall(
            name="observation_read",
            arguments={
                "observation_id": matches[0]["id"],
                "offset": matches[0]["match_offset"],
                "limit": 100,
            },
        )
    )
    assert fetched.status == "success", fetched
    page = json.loads(fetched.content)
    assert page["content"].startswith("719-ONYX")
    assert runtime.evidence.read_observation(runtime.state.run_id, page["id"])["sha256"] == digest(
        source
    )
    # Reading an archive must not recursively create archives of archive read responses.
    runtime.prepare_request()
    assert runtime.evidence.list_observations(runtime.state.run_id)["total"] == 19


def test_archive_failure_preserves_history_and_state_even_after_partial_writes(
    tmp_path, monkeypatch
):
    runtime = runtime_at(tmp_path)
    runtime._structured_history = exchange("first", "first diagnostic " * 1000)
    runtime._structured_history.extend(exchange("second", "irreplaceable evidence " * 1000))
    original_history = deepcopy(runtime._structured_history)
    original_state = runtime.state.model_dump()
    archive = runtime.evidence.archive_observation

    def fails_on_second(run_id, call_id, tool, content):
        if call_id == "second":
            raise OSError("disk full")
        return archive(run_id, call_id, tool, content)

    monkeypatch.setattr(runtime.evidence, "archive_observation", fails_on_second)
    with pytest.raises(OSError, match="disk full"):
        runtime.prepare_request()
    assert runtime._structured_history == original_history
    assert runtime.state.model_dump() == original_state
    assert runtime.evidence.list_observations(runtime.state.run_id)["total"] == 1


def test_repeated_compression_and_resume_keep_original_sources_and_stable_references(tmp_path):
    runtime = runtime_at(tmp_path)
    sources = {f"call-{i}": f"prefix {i} " * 800 + f"MIDDLE-{i}" + " tail" * 800 for i in range(12)}
    for call_id, source in sources.items():
        runtime._structured_history.extend(exchange(call_id, source))
    runtime.prepare_request()
    original = {
        ref["call_id"]: ref
        for ref in runtime.evidence.list_observations(runtime.state.run_id, limit=20)[
            "observations"
        ]
    }
    runtime._structured_history.extend(exchange("additional", "later observation " * 1000))
    prompt, tools, _report = runtime.prepare_request()
    runtime.checkpoint()
    restored = Runtime(tmp_path, runtime.settings)
    restored.resume(runtime.state.run_id)
    resumed_prompt, resumed_tools, _report = restored.prepare_request()
    assert (resumed_prompt, resumed_tools) == (prompt, tools)
    refs = {
        ref["call_id"]: ref
        for ref in restored.evidence.list_observations(restored.state.run_id, limit=20)[
            "observations"
        ]
    }
    assert len(refs) == len(sources) + 1
    for call_id, source in sources.items():
        assert refs[call_id] == original[call_id]
        marker = f"MIDDLE-{call_id.split('-')[1]}"
        page = restored.evidence.read_observation(
            restored.state.run_id, refs[call_id]["id"], query=marker
        )
        assert page["content"].startswith(marker) and page["sha256"] == digest(source)
    history_groups(restored._structured_history)


@pytest.mark.parametrize("damage", ["deleted", "modified"])
async def test_broken_archive_stops_before_sending_or_discarding_remaining_source(tmp_path, damage):
    runtime = runtime_at(tmp_path)
    runtime._structured_history = exchange("one-time", "unique original diagnostic " * 1000)
    runtime.prepare_request()
    ref = runtime.evidence.list_observations(runtime.state.run_id)["observations"][0]
    path = runtime.evidence.path("runs", runtime.state.run_id, "observations", ref["id"] + ".json")
    if damage == "deleted":
        path.unlink()
    else:
        record = json.loads(path.read_text())
        record["content"] = "forged evidence"
        path.write_text(json.dumps(record))
    previous_history = deepcopy(runtime._structured_history)
    previous_state = runtime.state.model_dump()
    with pytest.raises((OSError, ValueError)):
        runtime.prepare_request()
    assert runtime._structured_history == previous_history
    assert runtime.state.model_dump() == previous_state

    class MustNotSend:
        async def complete(self, *_):
            pytest.fail("An unavailable archive must stop before sending a model request")

    runtime.model = MustNotSend()
    state = await runtime.turn()
    assert state.stop_reason == "context_archive_unavailable" and state.phase == "failed"
    assert runtime._structured_history == previous_history


def test_legacy_observations_are_recoverable_without_inventing_calls(tmp_path):
    runtime = runtime_at(tmp_path)
    witness = "legacy launch code: MAPLE-43"
    runtime.state.messages = [
        {"role": "tool", "native": False, "content": witness + " old diagnostic" * 1000}
    ] + [{"role": "tool", "native": False, "content": f"legacy-{i} " * 1000} for i in range(12)]
    prompt, tools, report = runtime.prepare_request()
    assert report.removed_messages > 0
    from aster.context.prompt import is_context_note

    assert len(runtime._structured_history) == 1 and is_context_note(runtime._structured_history[0])
    assert witness not in str(prompt)
    assert "observation_read" not in {tool["name"] for tool in tools}
    catalog = next(tool for tool in tools if tool["name"] == "tool_load")
    assert "observation_read" in catalog["parameters"]["properties"]["names"]["items"]["enum"]
    listing = runtime.evidence.list_observations(runtime.state.run_id, query="MAPLE-43")
    assert len(listing["observations"]) == 1
    assert listing["observations"][0]["tool"] == "historical_observation"


@pytest.mark.parametrize("scenario", ["path", "other_run", "extra_run", "oversized_page"])
async def test_observation_tool_preserves_private_run_boundaries(tmp_path, scenario):
    runtime = runtime_at(tmp_path)
    run_id = runtime.state.run_id
    runtime.evidence.archive_observation(run_id, "local", "run_shell", "local observation")
    other = runtime.evidence.archive_observation("other", "foreign", "run_shell", "PRIVATE-CONTENT")
    arguments = {
        "path": {"observation_id": "../../other/checkpoint.json"},
        "other_run": {"observation_id": other["id"]},
        "extra_run": {"run_id": "other", "observation_id": other["id"]},
        "oversized_page": {"limit": 12001},
    }[scenario]
    outcome = await runtime.step(ToolCall(name="observation_read", arguments=arguments))
    assert outcome.status == "blocked" and "PRIVATE-CONTENT" not in outcome.content
    assert not runtime.state.read_evidence


async def test_archive_read_cannot_authorize_editing_a_file_changed_since_observation(tmp_path):
    path = tmp_path / "example.py"
    path.write_text("VALUE = 1\n")
    runtime = runtime_at(tmp_path)
    original = await runtime.step(ToolCall(name="read_file", arguments={"path": "example.py"}))
    assert original.status == "success"
    runtime.prepare_request()
    reference = runtime.evidence.list_observations(runtime.state.run_id)["observations"][0]
    path.write_text("VALUE = 2\n")
    historical = await runtime.step(
        ToolCall(name="observation_read", arguments={"observation_id": reference["id"]})
    )
    assert historical.status == "success" and "VALUE = 1" in historical.content
    assert "example.py" not in runtime.state.read_evidence
    edited = await runtime.step(
        ToolCall(
            name="patch_file",
            arguments={
                "path": "example.py",
                "old_text": "VALUE = 2",
                "new_text": "VALUE = 3",
            },
        )
    )
    assert edited.status == "blocked" and edited.reason == "fresh_read_required"
    assert path.read_text() == "VALUE = 2\n"


async def test_shortened_archive_ids_shown_to_the_model_resolve(tmp_path):
    import re

    runtime = Runtime(tmp_path, Settings(approval="auto", memory_enabled=False, context_tokens=64000,
                                         output_tokens=2048))
    runtime.start(TaskSpec(prompt="Find the cause using the evidence and report it."))
    witness = "masked witness: 4417-QUARTZ" + " detail" * 900
    ref = runtime.evidence.archive_observation(runtime.state.run_id, "early", "run_shell", witness)
    masked = runtime.context.calculator._mask_tool_output(
        exchange("early", witness)[1]["output"], reference={"id": ref["id"], "characters": ref["characters"]})
    runtime._structured_history = [exchange("early", witness)[0],
                                   {"type": "function_call_output", "call_id": "early", "output": masked}]
    prompt, _tools, _report = runtime.prepare_request()
    shown = re.findall(r"observation_id=([0-9a-f]+)", json.dumps(prompt))
    assert shown == [ref["id"][:16]]
    page = await runtime.step(ToolCall(name="observation_read", arguments={"observation_id": shown[0], "limit": 60}))
    assert page.status == "success", page
    assert json.loads(page.content)["content"].startswith("masked witness")
