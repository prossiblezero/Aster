import pytest

from aster.config import Settings
from aster.core.hooks import HookResult, Hooks
from aster.core.runtime import Runtime
from aster.core.verifier import Verifier
from aster.knowledge.memory import Memory
from aster.providers.codec import responses_reply
from aster.schema import ModelReply, TaskSpec, ToolCall
from aster.training.env import AsterEnv

pytestmark = pytest.mark.core


class FakeModel:
    def __init__(self, replies):
        self.replies = iter(replies)

    async def complete(self, messages, tools):
        return next(self.replies)


def reply(*calls, text=""):
    return ModelReply(
        text=text, calls=list(calls), raw=[{"role": "assistant", "content": text}] if text else []
    )


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_production_loop_write_then_verify_and_stale_verification(tmp_path):
    (tmp_path / "app.py").write_text("print('old')\n")
    (tmp_path / "test_app.py").write_text("def test_ok():\n    assert True\n")
    calls = [
        reply(ToolCall(name="read_file", arguments={"path": "app.py"}, call_id="r1")),
        reply(
            ToolCall(
                name="patch_file",
                arguments={"path": "app.py", "old_text": "old", "new_text": "new"},
                call_id="p1",
            )
        ),
        reply(
            ToolCall(
                name="run_tests",
                arguments={"command": "python -m pytest -q", "timeout": 10},
                call_id="t1",
            )
        ),
        reply(text="done"),
        reply(text="done after verification"),
    ]
    settings = Settings(approval="auto", completion="strict", sandbox="required")
    runtime = Runtime(tmp_path, settings, model=FakeModel(calls))
    state = await runtime.run(TaskSpec(prompt="Fix app.py and run tests", max_steps=10))
    assert state.phase == "completed"
    assert state.verification.state == "passed"
    assert (tmp_path / "app.py").read_text() == "print('new')\n"
    events = runtime.evidence.events(state.run_id)
    assert any(e["event"] == "verifier_result" for e in events)
    assert len(events) >= 12


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_completion_strictly_rejects_final_without_edit(tmp_path):
    runtime = Runtime(
        tmp_path,
        Settings(approval="auto", completion="strict"),
        model=FakeModel(
            [
                reply(text="I would edit it later"),
                reply(text="Still not edited"),
                reply(text="No edit"),
            ]
        ),
    )
    state = await runtime.run(TaskSpec(prompt="Implement a change in app.py", max_steps=2))
    assert state.phase == "stopped"
    assert state.stop_reason == "completion_requirements_failed"


@pytest.mark.asyncio
async def test_rl_uses_same_boundary(tmp_path):
    env = AsterEnv(tmp_path, Settings(approval="never", sandbox="required"))
    observation, info = env.reset(TaskSpec(prompt="inspect", max_steps=2))
    observation, reward, terminated, truncated, info = await env.step(
        ToolCall(name="write_file", arguments={"path": "x.txt", "content": "x"}, call_id="rl1")
    )
    assert reward < 0 and not terminated
    assert info["outcome"]["status"] == "blocked"
    assert not (tmp_path / "x.txt").exists()


def test_memory_evolution_requires_evidence_and_rolls_back(tmp_path):
    memory = Memory(tmp_path)
    assert memory.record("unsafe shortcut", run_id="r", evidence_id="", tags=[]) is None
    lesson = memory.record(
        "run pytest after edits",
        run_id="r",
        evidence_id="e1",
        tags=["tests"],
        observation="run pytest after edits",
    )
    assert lesson and memory.retrieve("tests")
    snapshot = memory.snapshot()
    memory.record("another lesson", run_id="r", evidence_id="e2", observation="another lesson")
    assert memory.rollback(snapshot)
    assert not any(x["text"] == "another lesson" for x in memory.state["lessons"])


def test_disabled_memory_does_not_recall_persisted_lessons(tmp_path):
    memory = Memory(tmp_path)
    memory.record(
        "deploy ring is violet", run_id="r", evidence_id="e1", observation="deploy ring is violet"
    )
    before = memory.path.read_bytes()
    disabled = Memory(tmp_path, enabled=False)
    assert disabled.retrieve("deploy") == []
    assert disabled.path.read_bytes() == before
    assert Memory(tmp_path).retrieve("deploy")[0]["text"] == "deploy ring is violet"


def test_verifier_infer_respects_read_only_language(tmp_path):
    inferred = Verifier.infer("Inspect config.txt and tell me the mode. Do not modify files.")
    assert inferred == {
        "require_edit": False,
        "require_verification": False,
        "require_observation": True,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("native,subtests", [(False, False), (True, False), (True, True)],
                         ids=["pytest", "native-unittest", "unittest-subtests"])
async def test_partial_test_success_cannot_erase_an_unfixed_regression(tmp_path, native, subtests):
    (tmp_path / "app.py").write_text("VALUE = 0\n")
    (tmp_path / "test_app.py").write_text(
        "from app import VALUE\n"
        "def test_regression(): assert VALUE == 1\n"
        "def test_control(): assert True\n"
    )
    full = "python -m pytest -q test_app.py"
    control, regression = full + "::test_control", full + "::test_regression"
    if native:
        check = "    def test_regression(self): self.assertEqual(VALUE, 1)\n"
        if subtests:
            check = ("    def test_regression(self):\n"
                     "        with self.subTest(case='value'): self.assertEqual(VALUE, 1)\n")
        (tmp_path / "test_app.py").write_text(
            "import unittest\nfrom app import VALUE\n"
            "class Check(unittest.TestCase):\n"
            + check +
            "    def test_control(self): self.assertTrue(True)\n"
        )
        (tmp_path / "runtests.py").write_text(
            "import sys, unittest\n"
            "suite = unittest.defaultTestLoader.loadTestsFromName(sys.argv[1])\n"
            "result = unittest.TextTestRunner().run(suite)\n"
            "raise SystemExit(not result.wasSuccessful())\n"
        )
        full = "python runtests.py test_app.Check"
        control, regression = full + ".test_control", full + ".test_regression"
    runtime = Runtime(tmp_path, Settings(approval="auto", memory_enabled=False))
    runtime.start(TaskSpec(prompt="Verify the behavior", require_verification=True))
    failed = await runtime.step(ToolCall(name="run_tests", arguments={
        "command": full,
    }))
    assert failed.reason == "tests_failed"
    passed = await runtime.step(ToolCall(name="run_tests", arguments={
        "command": control,
    }))
    assert passed.status == "success"
    assert not runtime.verifier.final(runtime.state).passed
    runtime.finalize("The selected test passed; done.")
    assert runtime.state.phase != "completed"
    assert "test_regression" in runtime.state.feedback

    await runtime.step(ToolCall(name="read_file", arguments={"path": "app.py"}))
    await runtime.step(ToolCall(name="patch_file", arguments={
        "path": "app.py", "old_text": "VALUE = 0", "new_text": "VALUE = 1",
    }))
    await runtime.step(ToolCall(name="run_tests", arguments={
        "command": control,
    }))
    assert not runtime.verifier.final(runtime.state).passed
    repaired = await runtime.step(ToolCall(name="run_tests", arguments={
        "command": regression,
    }))
    assert repaired.status == "success"
    assert runtime.verifier.final(runtime.state).passed


@pytest.mark.parametrize("replacement", [
    "import pytest\n@pytest.mark.skip(reason='omit')\ndef test_regression(): assert False\n",
    "",
], ids=["skipped", "deleted"])
async def test_skipping_or_deleting_a_failed_test_does_not_resolve_it(tmp_path, replacement):
    control = "def test_control(): assert True\n"
    path = tmp_path / "test_app.py"
    path.write_text("def test_regression(): assert False\n" + control)
    runtime = Runtime(tmp_path, Settings(approval="auto", memory_enabled=False))
    runtime.start(TaskSpec(prompt="Verify", require_verification=True))
    def call():
        return ToolCall(name="run_tests", arguments={"command": "python -m pytest -q"})
    assert (await runtime.step(call())).reason == "tests_failed"
    path.write_text(replacement + control)
    assert (await runtime.step(call())).status == "success"
    assert not runtime.verifier.final(runtime.state).passed


async def test_failed_cases_survive_checkpoint_resume_and_environment_reset(tmp_path):
    (tmp_path / "test_app.py").write_text(
        "def test_regression(): assert False\ndef test_control(): assert True\n"
    )
    settings = Settings(approval="auto", memory_enabled=False, reuse_environment=True)
    runtime = Runtime(tmp_path, settings)
    runtime.start(TaskSpec(prompt="Verify", require_verification=True))
    await runtime.step(ToolCall(name="run_tests", arguments={"command": "python -m pytest -q"}))
    runtime.sandbox.close()
    resumed = Runtime(tmp_path, settings)
    resumed.resume(runtime.state.run_id)
    assert (await resumed.step(ToolCall(name="run_tests", arguments={
        "command": "python -m pytest -q test_app.py::test_control",
    }))).status == "success"
    assert not resumed.verifier.final(resumed.state).passed
    resumed.sandbox.close()
    runtime.sandbox.close()


async def test_test_collection_error_does_not_become_a_permanent_case_failure(tmp_path):
    (tmp_path / "test_app.py").write_text("def test_control(): assert True\n")
    runtime = Runtime(tmp_path, Settings(approval="auto", memory_enabled=False))
    runtime.start(TaskSpec(prompt="Verify", require_verification=True))
    bad = await runtime.step(ToolCall(name="run_tests", arguments={
        "command": "python -m pytest --unknown-option",
    }))
    assert bad.status != "success"
    assert (await runtime.step(ToolCall(name="run_tests", arguments={
        "command": "python -m pytest -q",
    }))).status == "success"
    assert runtime.verifier.final(runtime.state).passed


def test_provider_reply_keeps_usage_for_cost_metrics():
    reply = responses_reply(
        {"model": "gpt-5.6-luna", "output_text": "ok", "output": []},
        {"input_tokens": 12, "output_tokens": 3, "total_tokens": 15},
    )
    assert reply.usage == {"input_tokens": 12, "output_tokens": 3, "total_tokens": 15}


@pytest.mark.asyncio
async def test_runtime_rejects_memory_evidence_not_in_trace(tmp_path):
    runtime = Runtime(tmp_path, Settings(approval="auto", memory_enabled=True), model=FakeModel([]))
    runtime.start(TaskSpec(prompt="remember"))
    outcome = await runtime.step(
        ToolCall(
            name="memory_store",
            arguments={"text": "untrusted", "evidence_id": "forged"},
            call_id="memory-1",
        )
    )
    assert outcome.status == "blocked"
    assert outcome.reason == "memory_provenance"


@pytest.mark.asyncio
async def test_read_file_updates_working_memory_without_reinjecting_summary(tmp_path):
    (tmp_path / "facts.txt").write_text("deploy key is red\n")
    runtime = Runtime(tmp_path, Settings(approval="auto"), model=FakeModel([]))
    runtime.start(TaskSpec(prompt="inspect facts.txt"))
    outcome = await runtime.step(ToolCall(name="read_file", arguments={"path": "facts.txt"}))
    assert outcome.status == "success"
    assert runtime.state.working_memory["facts.txt"]["summary"] == "deploy key is red"
    prompt, _ = runtime.context.build(
        runtime.state,
        runtime.state.task.prompt,
    )
    rendered = str(prompt)
    assert "deploy key is red" not in rendered
    assert "working_memory" not in rendered


@pytest.mark.asyncio
async def test_edit_invalidates_stale_working_memory(tmp_path):
    (tmp_path / "facts.txt").write_text("old fact\n")
    runtime = Runtime(tmp_path, Settings(approval="auto"), model=FakeModel([]))
    runtime.start(TaskSpec(prompt="edit facts.txt"))
    await runtime.step(ToolCall(name="read_file", arguments={"path": "facts.txt"}))
    await runtime.step(
        ToolCall(
            name="patch_file",
            arguments={"path": "facts.txt", "old_text": "old", "new_text": "new"},
        )
    )
    assert "facts.txt" not in runtime.state.working_memory


@pytest.mark.asyncio
async def test_resume_preserves_working_memory(tmp_path):
    (tmp_path / "facts.txt").write_text("persisted fact\n")
    runtime = Runtime(tmp_path, Settings(approval="auto"), model=FakeModel([]))
    state = runtime.start(TaskSpec(prompt="inspect facts.txt"))
    await runtime.step(ToolCall(name="read_file", arguments={"path": "facts.txt"}))
    run_id = state.run_id
    resumed = Runtime(tmp_path, Settings(approval="auto"))
    restored = resumed.resume(run_id)
    assert restored.working_memory["facts.txt"]["summary"] == "persisted fact"


@pytest.mark.asyncio
async def test_runtime_does_not_duplicate_tool_observation(tmp_path):
    (tmp_path / "facts.txt").write_text("one\n")
    runtime = Runtime(tmp_path, Settings(approval="auto"), model=FakeModel([]))
    runtime.start(TaskSpec(prompt="inspect facts.txt"))
    await runtime.step(ToolCall(name="read_file", arguments={"path": "facts.txt"}))
    assert sum(m["role"] == "tool" for m in runtime.state.messages) == 1


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_injected_hooks_run_and_fail_closed(tmp_path):
    class Plugin:
        def __init__(self):
            self.seen = []

        def before_tool(self, _state, call):
            self.seen.append(("before", call.name))
            return HookResult()

        def after_tool(self, _state, call, _outcome):
            self.seen.append(("after", call.name))
            return HookResult(feedback="hook note")

    plugin = Plugin()
    runtime = Runtime(
        tmp_path,
        Settings(approval="auto"),
        model=FakeModel([]),
        hooks=Hooks([plugin]),
    )
    runtime.start(TaskSpec(prompt="list files"))
    outcome = await runtime.step(ToolCall(name="list_files", arguments={}))
    assert outcome.status == "success"
    assert plugin.seen == [("before", "list_files"), ("after", "list_files")]
    assert any(m["role"] == "user" and m["content"] == "hook note" for m in runtime.state.messages)
