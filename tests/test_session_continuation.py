import asyncio
import json
import subprocess
import sys
from copy import deepcopy

import pytest

from aster.config import ModelProfile, Settings
from aster.context import ContextBudgetError
from aster.provider import ModelClient, ProviderError, request_payload
from aster.runtime import Runtime
from aster.schema import ModelReply, TaskSpec, ToolCall


def test_process_death_after_write_refuses_uncertain_resume(tmp_path):
    program = '''
import asyncio, os, sys
from pathlib import Path
from aster.config import Settings
from aster.runtime import Runtime
from aster.schema import TaskSpec, ToolCall
r = Runtime(Path(sys.argv[1]), Settings(approval="auto"))
r.start(TaskSpec(prompt="Write once"))
def die_after_execution(*args):
    os._exit(73)
r._finish_tool = die_after_execution
asyncio.run(r.step(ToolCall(name="write_file", call_id="uncertain-write",
    arguments={"path": "once.txt", "content": "written before process death"})))
'''
    result = subprocess.run([sys.executable, "-c", program, str(tmp_path)], timeout=20)
    assert result.returncode == 73
    assert (tmp_path / "once.txt").read_text() == "written before process death"
    run = next((tmp_path / ".aster/runs").iterdir())
    before = (run / "checkpoint.json").read_bytes()
    assert (run / "inflight.json").exists()
    restored = Runtime(tmp_path, Settings(approval="auto"))
    for adopt in [False, True]:
        with pytest.raises(ValueError, match="interrupted_tool_batch"):
            restored.resume(run.name, adopt_workspace=adopt)
    assert restored.state is None
    assert (run / "checkpoint.json").read_bytes() == before


def test_parent_resume_does_not_ignore_a_crashed_worker(tmp_path):
    program = '''
import asyncio, os, sys
from pathlib import Path
from aster.config import Settings
from aster.runtime import Runtime
from aster.schema import ModelReply, TaskSpec, ToolCall
class Writer:
    async def complete(self, *args):
        return ModelReply(calls=[ToolCall(name="write_file", call_id="child-write",
            arguments={"path":"child.txt", "content":"uncertain"})])
original = Runtime._finish_tool
def finish(self, call, *args):
    if call.name == "write_file":
        os._exit(73)
    return original(self, call, *args)
Runtime._finish_tool = finish
async def run():
    r = Runtime(Path(sys.argv[1]), Settings(approval="auto", max_workers=2), model=Writer())
    r.start(TaskSpec(prompt="Delegate"))
    result = await r.step(ToolCall(name="worker_start", arguments={
        "task":"write child.txt", "mode":"worker", "write_scope":["child.txt"]}))
    await r.workers.workers[result.data["worker_id"]].future
asyncio.run(run())
'''
    result = subprocess.run([sys.executable, "-c", program, str(tmp_path)], timeout=20)
    assert result.returncode == 73
    assert (tmp_path / "child.txt").read_text() == "uncertain"
    parent = next(
        path.parent for path in (tmp_path / ".aster/runs").glob("*/checkpoint.json")
        if not json.loads(path.read_text())["state"].get("parent_run_id")
    )
    assert not (parent / "inflight.json").exists()
    with pytest.raises(ValueError, match="interrupted_tool_batch"):
        Runtime(tmp_path, Settings(approval="auto", max_workers=2)).resume(
            parent.name, adopt_workspace=True
        )


async def test_invalid_later_tool_prevents_partial_batch_writes(tmp_path):
    class InvalidBatch:
        async def complete(self, *_):
            return ModelReply(calls=[
                ToolCall(name="write_file", arguments={"path": "good.txt", "content": "early"}),
                ToolCall(name="write_file", arguments={"path": "missing-content.txt"}),
            ])

    runtime = Runtime(tmp_path, Settings(approval="auto"), model=InvalidBatch())
    state = await runtime.run(TaskSpec(prompt="Write two files"))
    assert state.stop_reason == "context_history_invalid"
    assert "invalid_tool_arguments" in state.feedback
    assert not (tmp_path / "good.txt").exists()
    assert not runtime._structured_history and not state.used_call_ids


@pytest.mark.parametrize("ignore_cancellation", [False, True])
async def test_abort_settles_usage_in_report_and_checkpoint_once(tmp_path, ignore_cancellation):
    entered = asyncio.Event()
    cost = {"total_tokens": 12, "usage_requests": 1}

    class BilledClient(ModelClient):
        async def complete(self, *_args, **_kwargs):
            self.last_usage = cost.copy()
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                if not ignore_cancellation:
                    raise
            return ModelReply(usage=cost, calls=[ToolCall(
                name="write_file", arguments={"path": "late.txt", "content": "must not run"}
            )])

    settings = Settings(approval="auto")
    runtime = Runtime(tmp_path, settings, model=BilledClient(settings))
    pending = asyncio.create_task(runtime.run(TaskSpec(prompt="Write once")))
    await asyncio.wait_for(entered.wait(), 2)
    runtime.abort()
    try:
        await pending
    except asyncio.CancelledError:
        pass
    assert runtime.state.usage == cost and not (tmp_path / "late.txt").exists()
    run = runtime.evidence.path("runs", runtime.state.run_id)
    assert json.loads((run / "checkpoint.json").read_text())["state"]["usage"] == cost
    assert json.loads((run / "report.json").read_text())["usage"] == cost


async def test_committed_results_make_leftover_marker_safe(tmp_path):
    settings = Settings(approval="auto")
    runtime = Runtime(tmp_path, settings)
    state = runtime.start(TaskSpec(prompt="Write once"))
    call = ToolCall(name="write_file", call_id="completed-write", arguments={"path": "once.txt", "content": "once"})
    assert (await runtime.step(call)).status == "success"
    marker = runtime.evidence.path("runs", state.run_id, "inflight.json")
    assert not marker.exists()
    # Crash after atomic checkpoint replacement but before unlinking the marker.
    runtime._mark_inflight([call])
    restored = Runtime(tmp_path, settings)
    restored.resume(state.run_id)
    assert (await restored.step(call)).reason == "duplicate_call_id"
    assert (tmp_path / "once.txt").read_text() == "once"


def test_old_explicit_capabilities_restore_without_rewriting_checkpoint(tmp_path):
    settings = Settings(memory_enabled=True, memory_evolution=True, auto_dream=True, max_workers=2)
    runtime = Runtime(tmp_path, settings)
    state = runtime.start(TaskSpec(prompt="Remember project conventions"))
    checkpoint = runtime.evidence.path("runs", state.run_id, "checkpoint.json")
    data = json.loads(checkpoint.read_text())
    data.pop("checkpoint_version", None)  # v0.1 checkpoints remain readable.
    checkpoint.write_text(json.dumps(data))
    before = checkpoint.read_bytes()
    with pytest.raises(ValueError, match="runtime_identity_mismatch:.*auto_dream.*max_workers.*memory_enabled.*memory_evolution"):
        Runtime(tmp_path, Settings()).resume(state.run_id, allow_model_change=True)
    assert checkpoint.read_bytes() == before
    restored = Runtime(tmp_path, settings)
    restored.resume(state.run_id)
    assert restored.memory.enabled and restored.settings.max_workers == 2
    assert checkpoint.read_bytes() == before


def profile(protocol="chat", context=16000):
    return ModelProfile(
        provider="test",
        model="test-model",
        protocol=protocol,
        base_url="http://localhost:1234/v1",
        auth="none",
        context_tokens=context,
        output_tokens=512,
    )


async def test_recovery_keeps_successful_tools_and_original_request(tmp_path):
    class Interrupted:
        calls = 0

        async def complete(self, *_):
            self.calls += 1
            if self.calls == 1:
                return ModelReply(
                    calls=[
                        ToolCall(
                            name="write_file",
                            call_id="already-done",
                            arguments={"path": "answer.txt", "content": "answer"},
                        )
                    ]
                )
            raise ProviderError("interrupted", category="transport")

    runtime = Runtime(
        tmp_path,
        Settings(approval="auto", completion="off", memory_enabled=False),
        model=Interrupted(),
    )
    first = await runtime.run(
        TaskSpec(prompt="Write the original answer", max_steps=7, max_turns=4)
    )
    assert first.stop_reason == "provider_error" and first.changed_paths == ["answer.txt"]
    restored = Runtime(tmp_path, runtime.settings)
    restored.resume(first.run_id)
    next_state = restored.continue_task()
    assert next_state.session_id == first.session_id and next_state.run_id != first.run_id
    assert next_state.task.prompt == first.task.prompt and next_state.task.max_steps == 7
    assert next_state.changed_paths == ["answer.txt"] and next_state.used_call_ids == [
        "already-done"
    ]
    assert next_state.step == next_state.turn == 0 and next_state.usage == {}

    class Finish:
        async def complete(self, messages, _tools):
            wire = json.dumps(messages)
            assert "Write the original answer" in wire and "already-done" in wire
            assert "answer.txt" in wire
            return ModelReply(text="Recovered using the completed write")

    restored.model = Finish()
    final = await restored.run()
    assert final.phase == "completed" and final.step == 0
    assert (tmp_path / "answer.txt").read_text() == "answer"


async def test_feedback_invalidates_tests_but_explanation_does_not_require_an_edit(tmp_path):
    (tmp_path / "test_ok.py").write_text("def test_ok(): assert True\n")
    runtime = Runtime(tmp_path, Settings(approval="auto", memory_enabled=False))
    runtime.start(TaskSpec(prompt="Original request", require_verification=True))
    await runtime.step(ToolCall(name="run_tests"))
    runtime.finalize("done")
    old = runtime.state
    state = runtime.continue_task("Explain how the result works without modifying files")
    assert state.verification.state == "missing" and not state.task.require_edit
    assert not state.task.require_verification and state.read_evidence == {}
    assert old.verification.state == "passed"
    messages, _, _ = runtime.prepare_request()
    text = json.dumps(messages)
    assert "Original request" in text and "Explain how" in text


async def test_external_drift_requires_explicit_adoption_and_clears_read_evidence(tmp_path):
    (tmp_path / "a.txt").write_text("original")
    runtime = Runtime(tmp_path, Settings(approval="auto", profiles={"next": profile()}))
    runtime.start(TaskSpec(prompt="Inspect a.txt"))
    await runtime.step(ToolCall(name="read_file", arguments={"path": "a.txt"}))
    runtime.finish("cancelled")
    original = runtime.state
    (tmp_path / "a.txt").write_text("external")
    with pytest.raises(ValueError, match="workspace_mismatch"):
        runtime.continue_task()
    with pytest.raises(ValueError, match="workspace_mismatch"):
        runtime.switch_model("next")
    assert runtime.state is original
    state = runtime.continue_task(adopt_workspace=True)
    assert state.read_evidence == {} and state.verification.state == "missing"
    result = await runtime.step(
        ToolCall(
            name="patch_file",
            arguments={"path": "a.txt", "old_text": "external", "new_text": "overwritten"},
        )
    )
    assert result.reason == "fresh_read_required"
    assert (tmp_path / "a.txt").read_text() == "external"


@pytest.mark.parametrize(
    "source,target",
    [
        (a, b)
        for a in ["responses", "chat", "anthropic"]
        for b in ["responses", "chat", "anthropic"]
        if a != b
    ],
)
async def test_session_model_switch_converts_complete_history_and_preserves_native_evidence(
    tmp_path, source, target
):
    (tmp_path / "a.txt").write_text("source evidence")
    settings = Settings(
        protocol=source,
        model="old",
        approval="auto",
        memory_enabled=False,
        completion="off",
        profiles={"next": profile(target)},
    )
    runtime = Runtime(tmp_path, settings)
    runtime.start(TaskSpec(prompt="Inspect original file"))
    call = {"id": "old-call", "name": "read_file", "arguments": {"path": "a.txt"}}
    if source == "responses":
        native = [
            {
                "type": "function_call",
                "call_id": call["id"],
                "name": call["name"],
                "arguments": json.dumps(call["arguments"]),
            }
        ]
    elif source == "chat":
        native = [
            {
                "role": "assistant",
                "content": "checking",
                "tool_calls": [
                    {
                        "id": call["id"],
                        "type": "function",
                        "function": {
                            "name": call["name"],
                            "arguments": json.dumps(call["arguments"]),
                        },
                    }
                ],
            }
        ]
    else:
        native = [
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "checking"},
                    {
                        "type": "tool_use",
                        "id": call["id"],
                        "name": call["name"],
                        "input": call["arguments"],
                    },
                ],
            }
        ]
    runtime._structured_history = native
    runtime._in_turn = True
    outcome = await runtime._step(
        ToolCall(name=call["name"], arguments=call["arguments"], call_id=call["id"])
    )
    runtime._append_output(ToolCall(name=call["name"], call_id=call["id"]), outcome)
    runtime._in_turn = False
    runtime.finish("provider_error", failed=True)
    previous_history = deepcopy(runtime._structured_history)
    session = runtime.state.session_id
    runtime.switch_model("next")
    assert runtime.state.session_id == session and runtime.settings.protocol == target
    archived = json.loads(
        runtime.evidence.path(*runtime._history_origins[-1]["artifact"].split("/")).read_text()
    )
    assert archived["structured_history"] == previous_history
    runtime.continue_task("Explain the observed evidence")
    messages, tools, _ = runtime.prepare_request()
    payload = request_payload(
        messages, tools, protocol=target, model="test-model", output_tokens=512
    )
    assert "source evidence" in json.dumps(payload) and "old-call" in json.dumps(payload)
    assert runtime.settings.approval == "auto" and runtime.settings.sandbox == "required"


async def test_switch_small_window_or_invalid_history_rolls_back(tmp_path):
    runtime = Runtime(
        tmp_path, Settings(profiles={"small": profile(context=1024), "next": profile()})
    )
    runtime.start(TaskSpec(prompt="Inspect"))
    before = (runtime.settings, runtime.state, deepcopy(runtime._structured_history))
    with pytest.raises(ContextBudgetError):
        runtime.switch_model("small")
    assert runtime.settings is before[0] and runtime.state is before[1]
    assert runtime._structured_history == before[2]
    runtime._structured_history = [{"role": "assistant", "content": [{"type": "audio"}]}]
    with pytest.raises(ValueError, match="unsupported_history_content"):
        runtime.switch_model("next")
    assert runtime.settings is before[0] and runtime.state is before[1]


async def test_ancestor_output_archive_can_be_read_and_resumed_but_other_session_cannot(tmp_path):
    runtime = Runtime(tmp_path, Settings(approval="auto", memory_enabled=False, completion="off"))
    runtime.start(TaskSpec(prompt="Read output"))
    output = await runtime.step(
        ToolCall(
            name="run_shell",
            arguments={"command": 'python -c \'print("x" * 10000); print("tail evidence")\''},
        )
    )
    ref = output.data["observation"]["id"]
    runtime.finalize("done")
    state = runtime.continue_task("Find the prior tail evidence")
    run_id = state.run_id
    resumed = Runtime(tmp_path, runtime.settings)
    resumed.resume(run_id)
    page = await resumed.step(
        ToolCall(
            name="observation_read", arguments={"observation_id": ref, "query": "tail evidence"}
        )
    )
    assert json.loads(page.content)["content"] == "tail evidence\n"
    listing = await resumed.step(
        ToolCall(name="observation_read", arguments={"query": "tail evidence"})
    )
    assert ref in listing.content
    resumed.prepare_request()
    resumed.finish("cancelled")
    resumed.start(TaskSpec(prompt="A different session"))
    denied = await resumed.step(
        ToolCall(name="observation_read", arguments={"observation_id": ref})
    )
    assert denied.status == "blocked"


async def test_resume_requires_explicit_model_change_and_never_waives_permission_identity(tmp_path):
    runtime = Runtime(tmp_path, Settings(memory_enabled=False))
    runtime.start(TaskSpec(prompt="Inspect"))
    runtime.finish("cancelled")
    next_settings = Settings(model="different", protocol="chat", memory_enabled=False)
    restored = Runtime(tmp_path, next_settings)
    with pytest.raises(ValueError, match="runtime_identity_mismatch"):
        restored.resume(runtime.state.run_id)
    restored.resume(runtime.state.run_id, allow_model_change=True)
    restored.continue_task()
    unsafe = Runtime(tmp_path, next_settings.model_copy(update={"network": True}))
    with pytest.raises(ValueError, match="runtime_identity_mismatch"):
        unsafe.resume(runtime.state.run_id, allow_model_change=True)
    assert unsafe.state is None


async def test_legacy_ancestor_archive_survives_continuation_and_second_resume(tmp_path):
    (tmp_path / "a.txt").write_text("legacy evidence")
    settings = Settings(approval="auto", memory_enabled=False, completion="off")
    original = Runtime(tmp_path, settings)
    original.start(TaskSpec(prompt="Inspect a.txt"))
    await original.step(ToolCall(name="read_file", arguments={"path": "a.txt"}))
    original.prepare_request()
    original.finish("cancelled")
    checkpoint = original.evidence.path("runs", original.state.run_id, "checkpoint.json")
    data = json.loads(checkpoint.read_text())
    for name in ("provider", "profile", "auth", "stream", "read_parallelism"):
        data["runtime_identity"].pop(name)
    checkpoint.write_text(json.dumps(data))

    restored = Runtime(tmp_path, settings)
    restored.resume(original.state.run_id)
    continuation = restored.continue_task()
    assert restored._archive_sources
    observation_id = next(iter(restored._archive_sources))
    again = Runtime(tmp_path, settings)
    again.resume(continuation.run_id)
    result = await again.step(
        ToolCall(
            name="observation_read",
            arguments={"observation_id": observation_id, "query": "legacy evidence"},
        )
    )
    assert result.status == "success" and "legacy evidence" in result.content


def test_switch_new_credential_redacts_native_history_and_preview_archives(tmp_path, monkeypatch):
    secret = "fake-profile-next-credential"
    monkeypatch.setenv("ASTER_SESSION_TEST_KEY", secret)
    selected = ModelProfile(
        **{
            **profile().model_dump(),
            "auth": "required",
            "api_key_env": "ASTER_SESSION_TEST_KEY",
        }
    )
    runtime = Runtime(tmp_path, Settings(memory_enabled=False, profiles={"next": selected}))
    runtime.start(TaskSpec(prompt="Inspect the result"))
    runtime._structured_history = [
        {"role": "assistant", "content": secret},
        {
            "type": "function_call",
            "call_id": "read-secret",
            "name": "read_file",
            "arguments": '{"path":"a.txt"}',
        },
        {
            "type": "function_call_output",
            "call_id": "read-secret",
            "output": json.dumps({"status": "success", "content": secret}),
        },
    ]
    runtime.switch_model("next")
    run_directory = runtime.evidence.path("runs", runtime.state.run_id)
    assert list((run_directory / "observations").glob("*.json"))
    native = runtime.evidence.path(*runtime._history_origins[-1]["artifact"].split("/"))
    assert "[REDACTED]" in native.read_text()
    assert all(secret not in path.read_text() for path in run_directory.rglob("*.json"))


@pytest.mark.parametrize("source", ["responses", "anthropic"])
def test_switch_rejects_new_credential_in_opaque_history_without_mutation(
    tmp_path, monkeypatch, source
):
    secret = "fake-profile-next-credential"
    monkeypatch.setenv("ASTER_SESSION_TEST_KEY", secret)
    selected = ModelProfile(
        **{
            **profile().model_dump(),
            "auth": "required",
            "api_key_env": "ASTER_SESSION_TEST_KEY",
        }
    )
    runtime = Runtime(
        tmp_path, Settings(protocol=source, memory_enabled=False, profiles={"next": selected})
    )
    runtime.start(TaskSpec(prompt="Explain the result"))
    runtime._structured_history = (
        [{"type": "reasoning", "encrypted_content": secret}]
        if source == "responses"
        else [
            {
                "role": "assistant",
                "content": [{"type": "thinking", "thinking": "reasoning", "signature": secret}],
            }
        ]
    )
    state, settings = runtime.state, runtime.settings
    history = deepcopy(runtime._structured_history)
    evidence_redactor, memory_redactor = runtime.evidence.redactor, runtime.memory.redactor
    with pytest.raises(ValueError, match="opaque_history_contains_credential"):
        runtime.switch_model("next")
    assert runtime.state is state and runtime.settings is settings
    assert runtime._structured_history == history and runtime._history_origins == []
    assert runtime.evidence.redactor is evidence_redactor
    assert runtime.memory.redactor is memory_redactor
    assert not list(runtime.evidence.path("runs", state.run_id).glob("native-history-*.json"))


def test_resume_model_change_preserves_native_evidence_before_next_checkpoint(tmp_path):
    old = Runtime(tmp_path, Settings(memory_enabled=False))
    old.start(TaskSpec(prompt="Explain the result"))
    native = [
        {
            "type": "reasoning",
            "id": "original-reasoning",
            "encrypted_content": "native-evidence-only",
        },
        {"role": "assistant", "content": "Still working"},
    ]
    old._structured_history = deepcopy(native)
    old.checkpoint()
    restored = Runtime(
        tmp_path, Settings(protocol="chat", model="next-model", memory_enabled=False)
    )
    restored.resume(old.state.run_id, allow_model_change=True)
    restored.checkpoint()
    assert restored._history_origins
    origin = restored._history_origins[-1]
    archive = json.loads(restored.evidence.path(*origin["artifact"].split("/")).read_text())
    assert archive["structured_history"] == native
    assert archive["source"] == {
        "provider": "openai",
        "protocol": "responses",
        "model": old.settings.model,
    }
    assert restored._structured_history == [{"role": "assistant", "content": "Still working"}]
    again = Runtime(tmp_path, restored.settings)
    again.resume(restored.state.run_id)
    assert again._history_origins == restored._history_origins


@pytest.mark.parametrize(
    "feedback", ["Continue with the remaining work", "继续按原计划完成剩余工作"]
)
def test_natural_continuation_of_unfinished_task_keeps_required_acceptance(tmp_path, feedback):
    runtime = Runtime(tmp_path, Settings(memory_enabled=False))
    runtime.start(
        TaskSpec(
            prompt="Implement the requested fix and test it",
            require_observation=True,
            require_edit=True,
            require_verification=True,
            required_artifacts=["answer.txt"],
        )
    )
    runtime.finish("provider_error", failed=True)
    previous = runtime.state
    state = runtime.continue_task(feedback)
    assert state.task.require_observation and state.task.require_edit
    assert state.task.require_verification and state.task.required_artifacts == ["answer.txt"]
    assert not runtime.verifier.final(state).passed
    assert state.task.prompt == previous.task.prompt
    assert previous.stop_reason == "provider_error" and previous.phase == "failed"
    assert feedback in json.dumps(runtime.prepare_request()[0], ensure_ascii=False)
