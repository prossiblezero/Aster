"""Evaluation-only interpreter selection; preserve production Landlock restrictions."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from aster.safety.runner import SandboxRunner


def install(metadata: dict) -> None:
    python = metadata.get('test_python') or metadata.get('execution_python')
    if not python:
        return
    interpreter = Path(python).absolute()
    dependency = Path(metadata.get('dependency_root', interpreter.parent.parent)).resolve()
    if not interpreter.is_file() or not interpreter.is_relative_to(dependency):
        raise ValueError('invalid_task_interpreter')
    # Each episode is a separate OS process; no global settings escape this episode.
    original_environment = SandboxRunner.environment
    original_process = SandboxRunner._process
    # Venv interpreters depend on their read-only base stdlib as well as site-packages.
    from aster.safety import linux
    launcher_source = Path(linux.__file__).read_text().replace(
        "            sys.prefix,", "            sys.prefix,\n            sys.base_prefix,")
    if metadata.get('sandbox_file_limit_bytes'):
        launcher_source = launcher_source.replace('16 * 1024 * 1024', str(int(metadata['sandbox_file_limit_bytes'])))
    if metadata.get('sandbox_cpu_seconds'):
        limit = int(metadata['sandbox_cpu_seconds'])
        launcher_source = launcher_source.replace('(120, 120)', f'({limit}, {limit})')
    launcher_directory = tempfile.TemporaryDirectory(prefix="aster-eval-launcher-")
    launcher = Path(launcher_directory.name) / "linux.py"
    launcher.write_text(launcher_source)

    def environment(root, *, reuse=False):
        env = original_environment(root, reuse=reuse)
        env['PATH'] = os.pathsep.join([str(interpreter.parent), *metadata.get('extra_path', []), env['PATH']])
        env.update({key: value.replace('{workspace}', str(root.absolute()))
                    for key, value in metadata.get('execution_env', {}).items()})
        return env

    async def process(self, argv, cwd, timeout):
        if len(argv) > 2 and Path(argv[2]).name == 'linux.py':
            # Selecting the task interpreter makes its prefix readable through
            # the unchanged production launcher, without broad dependency binds.
            argv = [str(interpreter), argv[1], str(launcher), *argv[3:]]
            _keepalive = launcher_directory  # Retain launcher until this episode exits.
            assert _keepalive.name
        return await original_process(self, argv, cwd, timeout)

    SandboxRunner.environment = staticmethod(environment)
    SandboxRunner._process = process
