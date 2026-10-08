"""Offline packaging in the real required sandbox, with narrow system reads."""

import shlex
import sys
from pathlib import Path

import pytest

from aster.config import Settings
from aster.safety.runner import SandboxRunner
from aster.storage.workspace import Workspace


@pytest.mark.skipif(not Path("/etc/debian_version").is_file(), reason="Debian system metadata")
async def test_debian_version_read_does_not_allow_system_writes_or_other_etc_reads(tmp_path):
    (tmp_path / "check_system.py").write_text(
        """import os
from pathlib import Path

assert Path('/etc/debian_version').read_text().strip()
for path, flags in [('/etc/debian_version', os.O_WRONLY), ('/etc/passwd', os.O_RDONLY)]:
    try:
        descriptor = os.open(path, flags)
    except PermissionError:
        pass
    else:
        os.close(descriptor)
        raise AssertionError(f'Forbidden system access: {path}')
print('system boundaries preserved')
"""
    )
    result = await SandboxRunner(Settings(network=False, sandbox="required")).run(
        Workspace(tmp_path), f"{shlex.quote(sys.executable)} check_system.py", 5
    )
    assert result.status == "success", result.model_dump()
    assert result.content == "system boundaries preserved\n"
    assert result.data["isolation"] == "landlock+seccomp"


async def test_pip_builds_and_installs_local_package_offline_in_required_sandbox(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[build-system]\nrequires = ["setuptools", "wheel"]\n'
        'build-backend = "setuptools.build_meta"\n'
        '[project]\nname = "aster-sandbox-probe"\nversion = "0.0.1"\n'
        '[tool.setuptools]\npy-modules = ["sandbox_probe"]\n'
    )
    (tmp_path / "sandbox_probe.py").write_text("VALUE = 'installed from local wheel'\n")
    interpreter = shlex.quote(sys.executable)
    check = shlex.quote(
        "from pathlib import Path; import sandbox_probe; "
        "assert Path(sandbox_probe.__file__).resolve().parent == Path('install').resolve(); "
        "assert sandbox_probe.VALUE == 'installed from local wheel'; "
        "print('offline install verified')"
    )
    result = await SandboxRunner(Settings(network=False, sandbox="required")).run(
        Workspace(tmp_path),
        f"{interpreter} -m pip install --no-index --no-deps --no-build-isolation "
        "--no-cache-dir --disable-pip-version-check --target .tmp/install . "
        f"&& cd .tmp && PYTHONPATH=install {interpreter} -c {check}",
        30,
    )
    assert result.status == "success", result.model_dump()
    assert "offline install verified" in result.content
    assert result.data["isolation"] == "landlock+seccomp"
