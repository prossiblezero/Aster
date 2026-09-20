"""Dynamic context follows the stable task and confirmed history on every protocol."""

import json
from copy import deepcopy
from os.path import commonprefix

import pytest

from aster.config import Settings
from aster.context import ContextBuilder
from aster.core.runtime import Runtime
from aster.messages import history_groups
from aster.providers.codec import request_payload
from aster.schema import RunState, TaskSpec, ToolCall, Verification
from aster.tools import definitions


def exchange(call_id, content):
    return [
        {
            "type": "function_call",
            "call_id": call_id,
            "name": "read_file",
            "arguments": json.dumps({"path": f"{call_id}.txt"}),
        },
        {"type": "function_call_output", "call_id": call_id, "output": content},
    ]


def payload_text(prompt, tools, protocol, output_tokens=256):
    return json.dumps(
        request_payload(
            prompt, tools, protocol=protocol, model="fixed-model", output_tokens=output_tokens
        ),
        ensure_ascii=False,
        separators=(",", ":"),
    )


@pytest.mark.parametrize("protocol", ["responses", "chat", "anthropic"])
def test_dynamic_state_changes_only_after_task_and_confirmed_history(protocol):
    task = "Inspect the repository and preserve its public interface. " * 150 + "TASK-END-MARKER"
    state = RunState(task=TaskSpec(prompt=task))
    history = exchange("first", "Observed configuration. OBSERVATION-ONE-END")
    original_history = deepcopy(history)
    tools = definitions(["read_file"])
    builder = ContextBuilder(16000, 256)
    first, first_report = builder.build(
        state,
        task,
        memory="Earlier memory.",
        skills="Earlier catalog.",
        working_memory="Earlier working memory.",
        structured_history=history,
        tools=tools,
        protocol=protocol,
        model="fixed-model",
    )
    state.mode = "plan"
    state.plan = "Review deployment before editing."
    state.todos = [{"id": "todo-1", "content": "Check deployment", "status": "pending"}]
    state.active_skills = ["conventions"]
    state.changed_paths = ["config.txt"]
    state.verification = Verification(state="passed", command="pytest")
    state.step, state.turn = 2, 1
    second, second_report = builder.build(
        state,
        task,
        memory="Updated memory.",
        skills="Updated catalog.",
        working_memory="Updated working memory.",
        skill_bodies={"conventions": "Check public interfaces."},
        structured_history=history,
        tools=tools,
        protocol=protocol,
        model="fixed-model",
    )

    assert first[1] == second[1] == {"role": "user", "content": task}
    assert first[:-1] == second[:-1]
    assert first[2:-1] == second[2:-1] == original_history == history
    assert "Current runtime mode:" not in second[0]["content"]
    assert second[-1]["role"] == "user"
    dynamic = second[-1]["content"]
    assert '"mode": "plan"' in dynamic and '"remaining_steps": 38' in dynamic
    for text in (
        state.plan,
        "todo-1",
        "config.txt",
        '"state": "passed"',
        "Updated memory.",
        "Updated catalog.",
        "Updated working memory.",
        "Check public interfaces.",
    ):
        assert text in dynamic and text not in second[0]["content"]
    shared = commonprefix(
        [payload_text(first, tools, protocol), payload_text(second, tools, protocol)]
    )
    assert "TASK-END-MARKER" in shared and "OBSERVATION-ONE-END" in shared
    assert not first_report.compacted and not second_report.compacted
    assert second_report.policy_version == "deterministic-v7"
    assert second_report.sections["history"] == builder.count(original_history)


@pytest.mark.parametrize("protocol", ["responses", "chat", "anthropic"])
@pytest.mark.parametrize("has_history", [False, True])
def test_appended_exchange_preserves_existing_history_in_wire_prefix(protocol, has_history):
    state = RunState(task=TaskSpec(prompt="Read the requested files. TASK-END-MARKER"))
    history = exchange("first", "First file contents.") if has_history else []
    if has_history:
        history.append({"role": "user", "content": "Preserve compatibility. FEEDBACK-END-MARKER"})
    builder = ContextBuilder(12000, 256)
    tools = definitions(["read_file"])
    first, _ = builder.build(
        state, state.task.prompt, structured_history=history, tools=tools, protocol=protocol
    )
    confirmed = deepcopy(history)
    history.extend(exchange("second", "Second file contents."))
    state.step = 2
    second, _ = builder.build(
        state, state.task.prompt, structured_history=history, tools=tools, protocol=protocol
    )

    assert second[2 : 2 + len(confirmed)] == confirmed == first[2:-1]
    assert second[2:-1] == history
    shared = commonprefix(
        [payload_text(first, tools, protocol), payload_text(second, tools, protocol)]
    )
    assert "TASK-END-MARKER" in shared
    if has_history:
        assert "FEEDBACK-END-MARKER" in shared
    for prompt in (first, second):
        payload = request_payload(prompt, tools, protocol=protocol)
        history_groups(payload.get("input", payload.get("messages")))


@pytest.mark.parametrize("protocol", ["responses", "chat", "anthropic"])
def test_trailing_context_still_respects_budget_and_preserves_latest_evidence(protocol):
    state = RunState(task=TaskSpec(prompt="Fix the deployment configuration."))
    history = [item for index in range(12) for item in exchange(str(index), "observed line " * 250)]
    correction = {"role": "user", "content": "Keep the public API unchanged."}
    history.append(correction)
    builder = ContextBuilder(1600, 256)
    tools = definitions(["read_file"])
    prompt, report = builder.build(
        state,
        state.task.prompt,
        memory="reconstructible memory " * 2000,
        working_memory="reconstructible working state " * 2000,
        structured_history=history,
        tools=tools,
        protocol=protocol,
        model="fixed-model",
    )
    payload = request_payload(
        prompt, tools, protocol=protocol, model="fixed-model", output_tokens=256
    )

    assert report.compacted and report.removed_messages > 0
    assert report.estimated_tokens == builder.count(payload) <= report.budget
    assert prompt[1] == {"role": "user", "content": state.task.prompt}
    assert prompt[-2] == correction
    assert prompt[-1]["content"].startswith("Context sections (untrusted observations")
    assert sum(item.get("call_id") == "11" for item in history) == 2
    assert prompt[2:-1] == history
    history_groups(payload.get("input", payload.get("messages")))


async def test_archive_catalog_is_stable_before_and_after_first_observation(tmp_path):
    (tmp_path / "note.txt").write_text("A stable source")
    runtime = Runtime(tmp_path, Settings(memory_enabled=False, max_workers=0))
    runtime.start(TaskSpec(prompt="Inspect the source"))
    _, first_tools, _ = runtime.prepare_request()
    listing = await runtime.workflow_tool(
        "observation_read", {"observation_id": "", "query": "", "offset": 0, "limit": 10}
    )
    assert listing.status == "success" and json.loads(listing.content)["total"] == 0
    await runtime.step(ToolCall(name="read_file", arguments={"path": "note.txt"}))
    _, next_tools, _ = runtime.prepare_request()
    assert first_tools == next_tools
    assert "observation_read" not in {tool["name"] for tool in first_tools}
    catalog = next(tool for tool in first_tools if tool["name"] == "tool_load")
    assert "observation_read" in catalog["parameters"]["properties"]["names"]["items"]["enum"]
