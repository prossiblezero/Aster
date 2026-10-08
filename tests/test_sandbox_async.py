"""Real sandbox checks for local asyncio IPC without external socket access."""

import socket

from aster.config import Settings
from aster.safety.runner import SandboxRunner
from aster.storage.workspace import Workspace


async def test_asyncio_and_unittest_work_without_network(tmp_path):
    (tmp_path / "test_async.py").write_text(
        """import asyncio
import socket
import unittest

class AsyncChecks(unittest.IsolatedAsyncioTestCase):
    async def test_local_work(self):
        await asyncio.sleep(0)
        self.assertEqual(await asyncio.to_thread(lambda: 42), 42)
        left, right = socket.socketpair()
        with left, right:
            left.send(b"local")
            self.assertEqual(right.recv(5), b"local")
"""
    )
    result = await SandboxRunner(Settings(network=False, sandbox="required")).run(
        Workspace(tmp_path), "python -m unittest test_async", 5
    )
    assert result.status == "success", result.model_dump()
    assert "Ran 1 test" in result.content and "OK" in result.content
    assert result.data["isolation"] == "landlock+seccomp"


async def test_local_pairs_cannot_open_network_or_reach_host(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    secret = tmp_path / "host-secret.txt"
    secret.write_text("outside-boundary")
    host = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    host.set_inheritable(True)
    try:
        (root / "check_boundary.py").write_text(
            f"""import errno
import socket

def denied(operation):
    try:
        operation()
    except PermissionError as exc:
        assert exc.errno in (errno.EPERM, errno.EACCES)
    else:
        raise AssertionError("sandbox permitted a forbidden operation")

for family in (socket.AF_INET, socket.AF_INET6, socket.AF_UNIX):
    denied(lambda: socket.socket(family))
for family in (socket.AF_INET, socket.AF_INET6):
    denied(lambda: socket.socketpair(family))
left, right = socket.socketpair()
with left, right:
    denied(lambda: left.connect({str(tmp_path / "host.sock")!r}))
    denied(lambda: left.bind({str(tmp_path / "host.sock")!r}))
    denied(lambda: left.sendto(b"outside", {str(tmp_path / "host.sock")!r}))
    denied(lambda: left.sendmsg([b"outside"]))
denied(lambda: open({str(secret)!r}).read())
try:
    inherited = socket.socket(fileno={host.fileno()})
except OSError:
    pass
else:
    raise AssertionError("host socket descriptor was inherited")
print("boundaries preserved")
"""
        )
        result = await SandboxRunner(Settings(network=False, sandbox="required")).run(
            Workspace(root), "python check_boundary.py", 5
        )
        assert result.status == "success", result.model_dump()
        assert "boundaries preserved" in result.content
    finally:
        host.close()
