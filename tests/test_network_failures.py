"""Failures caused only by an unreachable network are environment facts, not code defects."""

import pytest

from aster.config import Settings
from aster.core.runtime import Runtime
from aster.schema import TaskSpec, ToolCall
from aster.tools.testing import network_unavailable, parse_junit

pytestmark = pytest.mark.core

UNREACHABLE = """>       sock.connect(sa)
E       OSError: [Errno 101] Network is unreachable
/site-packages/urllib3/util/connection.py:85: OSError"""


def test_unreachable_network_needs_a_real_socket_frame():
    assert network_unavailable(UNREACHABLE)
    assert network_unavailable("socket.gaierror: [Errno -3] Temporary failure in name resolution\n"
                               "    for res in socket.getaddrinfo(host, port):")
    # The same text raised directly by test code is not evidence of missing network.
    assert not network_unavailable("raise OSError(101, 'Network is unreachable')\n"
                                   "E   OSError: [Errno 101] Network is unreachable")
    assert network_unavailable(">       _socket.socket.__init__(self, family, type, proto, fileno)\n"
                               "E       PermissionError: [Errno 1] Operation not permitted")
    assert not network_unavailable("E       PermissionError: [Errno 1] Operation not permitted: 'out.txt'")
    # A refused connection can mean a service the task must start is not running.
    assert not network_unavailable(">       sock.connect(sa)\nE   ConnectionRefusedError: [Errno 111] Connection refused")


def test_junit_keeps_failure_detail():
    cases = parse_junit(
        '<testsuite><testcase classname="t" name="a"><failure message="boom">'
        + UNREACHABLE.replace("<", "&lt;").replace(">", "&gt;")
        + '</failure></testcase><testcase classname="t" name="b"/></testsuite>'
    )
    assert cases[0]["status"] == "failed" and "Network is unreachable" in cases[0]["detail"]
    assert cases[1]["detail"] == ""


@pytest.mark.asyncio
async def test_network_only_failures_do_not_block_completion(tmp_path):
    (tmp_path / "test_net.py").write_text(
        "import socket\n"
        "def test_remote():\n"
        "    socket.create_connection(('10.255.255.1', 80), timeout=2)\n"
        "def test_local():\n"
        "    assert True\n"
    )
    runtime = Runtime(tmp_path, Settings(approval="auto", memory_enabled=False, sandbox="required",
                                         network=False))
    runtime.start(TaskSpec(prompt="Verify the behavior", require_verification=True))
    outcome = await runtime.step(ToolCall(name="run_tests", arguments={"command": "python -m pytest -q"}))
    assert outcome.data["test_results"] == {"pytest: test_net::test_remote": "network_unavailable",
                                            "pytest: test_net::test_local": "passed"}
    assert "network is unreachable" in outcome.content
    assert runtime.state.verification_failures == {}
    assert list(runtime.state.environment_failures) == ["pytest: test_net::test_remote"]
    assert runtime.state.verification.state == "passed"
    result = runtime.verifier.final(runtime.state)
    assert result.passed and "pytest: test_net::test_remote" in result.details["environment_failures"]


@pytest.mark.asyncio
async def test_refused_and_mocked_failures_still_block(tmp_path):
    (tmp_path / "test_net.py").write_text(
        "import socket\n"
        "def test_refused():\n"
        "    socket.create_connection(('127.0.0.1', 1), timeout=2)\n"
        "def test_mocked():\n"
        "    raise OSError(101, 'Network is unreachable')\n"
    )
    # Sockets are allowed here (only loopback is contacted), so refusal is a real failure.
    runtime = Runtime(tmp_path, Settings(approval="auto", memory_enabled=False, sandbox="required",
                                         network=True))
    runtime.start(TaskSpec(prompt="Verify the behavior", require_verification=True))
    outcome = await runtime.step(ToolCall(name="run_tests", arguments={"command": "python -m pytest -q"}))
    assert set(outcome.data["test_results"].values()) == {"failed"}
    assert set(runtime.state.verification_failures) == {"pytest: test_net::test_refused",
                                                        "pytest: test_net::test_mocked"}
    assert runtime.state.verification.state == "failed"
    assert not runtime.verifier.final(runtime.state).passed
