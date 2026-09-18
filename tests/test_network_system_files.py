"""Authorized DNS gets exact read-only resolver files, never a broad /etc grant."""

import shlex
import sys
from pathlib import Path

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


@pytest.mark.skipif(not Path("/etc/mime.types").is_file(), reason="System MIME database absent")
@pytest.mark.parametrize("network", [False, True])
async def test_mimetypes_uses_exact_read_only_database_without_etc_access(tmp_path, network):
    program = """import mimetypes
import os
from pathlib import Path

assert mimetypes.MimeTypes().guess_type('report.csv')[0] == 'text/csv'
assert Path('/etc/mime.types').read_bytes()
for name, mode in [('/etc/mime.types', os.O_WRONLY), ('/etc/passwd', os.O_RDONLY)]:
    try:
        descriptor = os.open(name, mode)
    except PermissionError:
        pass
    else:
        os.close(descriptor)
        raise AssertionError('unauthorized system access: ' + name)
print('MIME database read-only; unrelated system files denied')
"""
    result = await SandboxRunner(Settings(network=network)).run(
        Workspace(tmp_path), shlex.join([sys.executable, "-c", program]), 10
    )
    assert result.status == "success", result.model_dump()
    assert result.content == "MIME database read-only; unrelated system files denied\n"
    assert result.data["isolation"] == "landlock+seccomp"
