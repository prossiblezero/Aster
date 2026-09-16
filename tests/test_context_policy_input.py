"""External policies receive exactly the request used by autonomous execution."""

import json

from aster.config import Settings
from aster.context import ContextBuilder
from aster.provider import history_groups
from aster.rl import AsterEnv
from aster.schema import ModelReply, RunState, TaskSpec, ToolCall


async def test_rollout_and_autonomous_request_match_after_tool_execution(tmp_path):
    (tmp_path / "config.txt").write_text("timeout=12\n")
    env = AsterEnv(tmp_path, Settings(approval="auto", auto_dream=False, completion="off"))
    initial, _ = env.reset("Inspect the configuration.")
    assert initial["model_input"] and initial["context_policy"] == "deterministic-v2"
    observation, _, _, _, _ = await env.step(
        ToolCall(name="read_file", arguments={"path": "config.txt"})
    )
    history_groups(observation["model_input"])

    class Capture:
        async def complete(self, messages, tools):
            assert messages == observation["model_input"]
            assert tools == observation["tools"]
            return ModelReply(text="Read the configuration.")

    env.runtime.model = Capture()
    await env.runtime.turn()
    assert env.runtime.state.phase == "completed"
    env.close()


async def test_archive_failure_is_a_graded_environment_stop(tmp_path):
    (tmp_path / "facts.txt").write_text("known fact\n")
    env = AsterEnv(tmp_path, Settings(approval="auto", auto_dream=False))
    env.reset("Inspect facts.")
    await env.step(ToolCall(name="read_file", arguments={"path": "facts.txt"}))
    runtime = env.runtime
    record = json.loads(runtime._structured_history[-1]["output"])
    runtime.evidence.path(
        "runs", runtime.state.run_id, "observations", record["observation"]["id"] + ".json"
    ).unlink()
    observation, reward, terminated, truncated, info = await env.step(ToolCall(name="list_files"))
    assert terminated and not truncated and reward == -1
    assert info["stop_reason"] == "context_archive_unavailable"
    assert observation["model_input"] == []
    env.close()


async def test_rejected_archive_read_remains_a_regular_tool_error(tmp_path):
    env = AsterEnv(tmp_path, Settings(approval="auto", auto_dream=False))
    env.reset("Inspect files.")
    await env.step(ToolCall(name="list_files"))
    observation, _, terminated, truncated, info = await env.step(
        ToolCall(name="observation_read", arguments={"observation_id": "invalid"})
    )
    assert info["outcome"]["reason"] == "observation_unavailable"
    assert not terminated and not truncated and observation["model_input"]
    assert "invalid_observation_id" in str(observation["model_input"])
    env.close()


def test_compacted_archive_page_cursor_covers_exactly_the_visible_text():
    source = "部署配置与最终约束。" * 200
    builder = ContextBuilder(4096, 512, 128)
    original = {
        "id": "a" * 64,
        "offset": 7,
        "next_offset": 7 + len(source),
        "total": 9000,
        "content": source,
    }
    envelope = json.dumps(
        {
            "status": "success",
            "page": "observation",
            "content": json.dumps(original, ensure_ascii=False),
        },
        ensure_ascii=False,
    )
    clipped = json.loads(builder._clip_tool_output(envelope))
    page = json.loads(clipped["content"])
    assert page["content"] and source.startswith(page["content"])
    assert page["next_offset"] == 7 + len(page["content"]) < original["next_offset"]
    assert builder.count(clipped["content"]) <= 128


def test_user_correction_does_not_make_latest_tool_batch_disposable():
    state = RunState(task=TaskSpec(prompt="Finish the task."))
    history = []
    for i in range(20):
        history.extend(
            [
                {
                    "type": "function_call",
                    "call_id": str(i),
                    "name": "read_file",
                    "arguments": "{}",
                },
                {
                    "type": "function_call_output",
                    "call_id": str(i),
                    "output": "observed line\n" * 200,
                },
            ]
        )
    history.append({"role": "user", "content": "Keep the public API unchanged."})
    prompt, report = ContextBuilder(1300, 128).build(
        state, state.task.prompt, structured_history=history
    )
    assert report.removed_messages > 0
    assert sum(item.get("call_id") == "19" for item in prompt) == 2
    assert prompt[-1]["content"] == "Keep the public API unchanged."
    history_groups(prompt)
