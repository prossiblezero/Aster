"""A child must spend its last affordable request delivering observed work."""

import asyncio
import json

import pytest

from aster.config import Settings
from aster.core.runtime import Runtime
from aster.schema import ModelReply, TaskSpec, ToolCall


@pytest.mark.parametrize("reply_kind", ["answer", "unknown_usage", "unsolicited_tool"])
async def test_child_preserves_findings_before_token_budget_rejects_next_request(tmp_path, reply_kind):
    (tmp_path / "target.py").write_text("answer = 42\n" + "# inspected implementation\n" * 700)
    requests = []

    class Model:
        async def complete(self, messages, tools):
            requests.append((messages, tools))
            return ModelReply(
                text="" if reply_kind == "unsolicited_tool" else
                "Partial: target.py defines answer = 42; other files remain unchecked.",
                calls=[ToolCall(name="write_file", arguments={"path": "target.py", "content": "bad"})]
                if reply_kind == "unsolicited_tool" else [],
                usage={} if reply_kind == "unknown_usage" else {"total_tokens": 1000},
            )

    parent = Runtime(tmp_path, Settings(
        max_workers=1, completion="off", output_tokens=512, context_tokens=64000,
    ), model=Model())
    parent.start(TaskSpec(prompt="Inspect the repository", max_turns=36, max_steps=60))
    try:
        started = await parent.workers.start("Inspect target.py and report remaining gaps")
        worker = parent.workers.workers[started["worker_id"]]
        child = worker.runtime
        # Execute a real read before the scheduled child starts. The next request
        # is much larger than a previous estimate, so the decision must count it.
        await child.step(ToolCall(name="read_file", arguments={"path": "target.py"}))
        _, _, report = child.prepare_request()
        request_tokens = report.estimated_tokens + child.settings.effective_output_tokens
        child._request_estimate = 1
        parent.state.task.max_tokens = 4 * request_tokens
        parent.state.team_usage["charged_tokens"] = 2 * request_tokens
        before = parent.state.team_usage["charged_tokens"]
        await asyncio.wait_for(worker.future, 5)
        delivered = await parent.workers.status(worker.worker_id)
        assert len(requests) == 1 and requests[0][1] == []
        assert "PARTIAL handoff" in json.dumps(requests[0][0])
        assert "answer = 42" in json.dumps(requests[0][0])
        assert delivered["delivery"] == "partial"
        assert delivered["stop_reason"] == "team_token_limit"
        assert child.state.phase == "stopped" and child.state.step == 1
        assert delivered["answer"] and delivered["read_evidence"]
        assert (tmp_path / "target.py").read_text().startswith("answer = 42\n")
        if reply_kind == "unknown_usage":
            assert parent.state.team_usage["estimated_requests"] == 1
            assert parent.state.team_usage["charged_tokens"] == before + child._request_estimate
        else:
            assert parent.state.team_usage["charged_tokens"] == before + 1000
        assert parent.state.team_usage["model_calls"] == 1
        assert parent.state.team_usage["charged_tokens"] + parent.workers.budget.token_reserve() <= parent.state.task.max_tokens
        # A partial handoff is not permission to finish an unobserved parent task.
        prompt, tools, _ = parent.prepare_request()
        assert tools and delivered["answer"] in json.dumps(prompt)
        assert not parent.state.terminal
    finally:
        await parent.workers.close()
        parent.sandbox.close()


@pytest.mark.parametrize("max_tokens", [None, 100000])
async def test_child_with_enough_budget_can_complete_normally(tmp_path, max_tokens):
    requests = []

    class Model:
        async def complete(self, messages, tools):
            requests.append(tools)
            return ModelReply(text="Task complete.", usage={"total_tokens": 100})

    parent = Runtime(tmp_path, Settings(max_workers=1, completion="off"), model=Model())
    parent.start(TaskSpec(prompt="Summarize", max_tokens=max_tokens))
    try:
        started = await parent.workers.start("Summarize the supplied request")
        worker = parent.workers.workers[started["worker_id"]]
        await asyncio.wait_for(worker.future, 5)
        delivered = await parent.workers.status(worker.worker_id)
        assert len(requests) == 1 and requests[0]
        assert delivered["delivery"] == "complete" and delivered["answer"] == "Task complete."
    finally:
        await parent.workers.close()
        parent.sandbox.close()
