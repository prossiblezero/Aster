"""Operation receipts survive resumes without reapplying edits or fresh test evidence."""

import asyncio
import json
import shlex
import sys

import pytest

from aster.config import Settings
from aster.core.runtime import Runtime
from aster.schema import Outcome, TaskSpec, ToolCall, Verification
from aster.tools.registry import ToolRegistry

pytestmark = pytest.mark.core


async def test_blocked_publication_replays_receipt_without_repeating_http_effect(tmp_path):
    requests = []

    async def receive(reader, writer):
        requests.append(await reader.readuntil(b"\r\n\r\n"))
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    async with await asyncio.start_server(receive, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        program = (
            "import urllib.request; from pathlib import Path; "
            f"urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:{port}/', "
            "data=b'', method='POST')).close(); "
            "Path('.env').write_text('synthetic protected output')"
        )
        runtime = Runtime(tmp_path, Settings(approval="auto", memory_enabled=False, network=True))
        runtime.start(TaskSpec(prompt="Record the external operation"))

        def call():
            return ToolCall(name="run_shell", arguments={
                "command": f"{shlex.quote(sys.executable)} -c {shlex.quote(program)}",
            }, idempotency_key="post-once")

        original = await runtime.step(call())
        assert (original.status, original.reason) == ("blocked", "sandbox_protected_output")
        replay = await runtime.step(call())
        assert len(requests) == 1
        assert replay.status == "blocked" and replay.data["idempotency_replayed"]


async def test_write_key_replays_after_resume_and_rejects_changed_arguments(tmp_path):
    settings = Settings(approval="auto", memory_enabled=False, completion="off")
    runtime = Runtime(tmp_path, settings)
    runtime.start(TaskSpec(prompt="Record an operation"))

    def request(content="one"):
        return ToolCall(name="write_file", arguments={"path": "once.txt", "content": content,
                                                       "_aster_idempotency_key": "create-once"})

    original = await runtime.step(request())
    assert original.status == "success"
    restored = Runtime(tmp_path, settings)
    restored.resume(runtime.state.run_id)
    (tmp_path / "once.txt").write_text("external edit")
    replay = await restored.step(request())
    assert replay.status == "success" and replay.data["idempotency_replayed"]
    assert not replay.changed_paths and (tmp_path / "once.txt").read_text() == "external edit"
    assert json.loads(restored._structured_history[-1]["output"])["idempotency_replayed"]
    conflict = await restored.step(request("different"))
    assert conflict.reason == "idempotency_conflict" and (tmp_path / "once.txt").read_text() == "external edit"


async def test_receipt_cannot_mark_old_tests_as_fresh(tmp_path, monkeypatch):
    executed = []

    async def execute(registry, call):
        executed.append(call)
        return Outcome(status="success", content="old tests passed", data={"is_verification": True})

    monkeypatch.setattr(ToolRegistry, "execute", execute)
    runtime = Runtime(tmp_path, Settings(approval="auto", memory_enabled=False))
    runtime.start(TaskSpec(prompt="Run tests"))
    def call():
        return ToolCall(name="run_tests", arguments={"command": "python -m pytest"}, idempotency_key="check-once")
    assert (await runtime.step(call())).status == "success"
    assert runtime.state.verification.state == "passed"
    runtime.state.verification = Verification()
    replay = await runtime.step(call())
    assert replay.status == "success" and len(executed) == 1
    assert runtime.state.verification.state == "missing"


async def test_cancelled_effect_keeps_uncertain_receipt(tmp_path, monkeypatch):
    entered = asyncio.Event()
    count = []

    async def execute(registry, call):
        count.append(call)
        (tmp_path / "remote-effect.txt").write_text("effect already happened")
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(ToolRegistry, "execute", execute)
    runtime = Runtime(tmp_path, Settings(approval="auto", memory_enabled=False))
    runtime.start(TaskSpec(prompt="Original task"))
    def call():
        return ToolCall(name="write_file", arguments={"path": "remote-effect.txt", "content": "x"},
                        idempotency_key="uncertain-operation")
    running = asyncio.create_task(runtime.step(call()))
    await entered.wait()
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    runtime.continue_task()
    blocked = await runtime.step(call())
    assert blocked.reason == "idempotency_uncertain" and len(count) == 1
