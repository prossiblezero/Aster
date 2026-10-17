"""Long-running services survive tool calls inside the sandbox and end with the task."""

import asyncio
import socket

import pytest

from aster.config import Settings
from aster.core.runtime import Runtime
from aster.schema import TaskSpec, ToolCall

pytestmark = pytest.mark.core

SERVER = (
    "import socket\n"
    "s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)\n"
    "s.bind(('127.0.0.1', PORT)); s.listen(); print('ready', flush=True)\n"
    "while True:\n"
    "    c, _ = s.accept(); c.sendall(b'pong'); c.close()\n"
)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def runtime_for(root):
    runtime = Runtime(root, Settings(approval="auto", memory_enabled=False, sandbox="required", network=True,
                                     completion="off"), approval_callback=lambda *_: True)
    runtime.start(TaskSpec(prompt="serve"))
    return runtime


async def call(runtime, tool, **arguments):
    return await runtime.step(ToolCall(name=tool, arguments=arguments))


def ping(port):
    with socket.create_connection(("127.0.0.1", port), timeout=3) as conn:
        return conn.recv(4)


@pytest.mark.asyncio
@pytest.mark.parametrize("keep", [False, True])
async def test_service_survives_tool_calls_and_ends_with_the_task_unless_kept(tmp_path, keep):
    port = free_port()
    (tmp_path / "server.py").write_text(SERVER.replace("PORT", str(port)))
    runtime = runtime_for(tmp_path)
    started = await call(runtime, "service_start", name="web", command="python3 server.py", keep_running=keep)
    assert started.status == "success" and "running" in started.content and "ready" in started.content
    await call(runtime, "run_shell", command="echo another command")  # would kill a plain background job
    assert ping(port) == b"pong"
    status = await call(runtime, "service_status", name="web")
    assert "web: running" in status.content
    runtime.finalize("Server is up.")
    await asyncio.sleep(0.5)
    if keep:
        assert ping(port) == b"pong" and "Services left running: web" in runtime.state.final
        runtime.sandbox.stop_service("web")
    else:
        with pytest.raises(OSError):
            ping(port)


@pytest.mark.asyncio
async def test_a_failing_service_reports_its_output(tmp_path):
    runtime = runtime_for(tmp_path)
    result = await call(runtime, "service_start", name="bad", command="python3 -c 'raise SystemExit(\"boom\")'")
    assert result.reason == "service_exited" and "boom" in result.content


@pytest.mark.asyncio
async def test_service_tools_are_loadable_not_core(tmp_path):
    runtime = runtime_for(tmp_path)
    names = {tool["name"] for tool in runtime.tool_definitions()}
    assert "service_start" not in names
    loader = next(tool for tool in runtime.tool_definitions() if tool["name"] == "tool_load")
    assert "service_start" in loader["parameters"]["properties"]["names"]["items"]["enum"]
