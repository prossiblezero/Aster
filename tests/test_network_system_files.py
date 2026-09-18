"""Authorized DNS gets exact read-only resolver files, never a broad /etc grant."""

import shlex
import sys

import pytest

from aster.config import Settings
from aster.safety.runner import SandboxRunner
from aster.workspace import Workspace


@pytest.mark.parametrize("network", [False, True])
async def test_resolver_files_follow_network_setting_and_remain_read_only(tmp_path, network):
    program = f"""import os
from pathlib import Path
network = {network!r}
for name in ('/etc/resolv.conf', '/etc/hosts', '/etc/nsswitch.conf', '/etc/gai.conf'):
    try:
        Path(name).read_bytes()
    except FileNotFoundError:
        continue
    except PermissionError:
        assert not network, name
    else:
        assert network, name
        try:
            fd = os.open(name, os.O_WRONLY)
        except PermissionError:
            pass
        else:
            os.close(fd)
            raise AssertionError('resolver file writable: ' + name)
try:
    Path('/etc/passwd').read_bytes()
except PermissionError:
    pass
else:
    raise AssertionError('unrelated system file exposed')
print('resolver boundaries preserved')
"""
    result = await SandboxRunner(Settings(network=network)).run(
        Workspace(tmp_path), shlex.join([sys.executable, "-c", program]), 10
    )
    assert result.status == "success", result.model_dump()
    assert result.content == "resolver boundaries preserved\n"
    assert result.data["isolation"] == "landlock+seccomp"
