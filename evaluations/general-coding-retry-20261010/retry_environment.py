"""Container-only diagnostic adaptations; production Aster remains frozen."""
import asyncio
import os
import signal
import subprocess
import tempfile
import time
from pathlib import Path

from aster.safety.runner import OUTPUT_LIMIT, SandboxRunner
from aster.schema import Outcome
from aster.storage.workspace import Workspace
from aster.tools import schemas


def install(mode):
    original_capabilities = SandboxRunner.capabilities
    if mode == 'pytest':
        SandboxRunner.capabilities = lambda self: original_capabilities(self) + (
            ' The task repository uses src-layout. Its editable import follows the current '
            'execution workspace. Use workspace-relative shell/test paths; do not cd /testbed '
            'from the copied execution workspace.')
        return
    if mode != 'grpc':
        return
    original_run = SandboxRunner._run

    def command_in_container(self, command, root, timeout):
        # Only this disposable, network-isolated task container owns these processes.
        # Leave successful background children for grading; container removal reaps them.
        (root / '.tmp').mkdir(exist_ok=True)
        started = time.monotonic()
        with tempfile.TemporaryFile() as output:
            process = subprocess.Popen(
                ['/bin/sh', '-c', command], cwd=root, env=self.environment(root),
                stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                start_new_session=True)
            try:
                code = process.wait(timeout=timeout)
                status = 'success' if code == 0 else 'error'
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                code = process.wait()
                status = 'timeout'
            size = output.tell()
            output.seek(0)
            content = output.read(OUTPUT_LIMIT).decode(errors='replace')
        return Outcome(status=status, exit_code=code, content=content,
                       duration_ms=(time.monotonic() - started) * 1000,
                       data={'isolation': 'dedicated_docker_container',
                             'background_processes_persist': True,
                             'output_bytes': size, 'captured_bytes': min(size, OUTPUT_LIMIT),
                             'output_truncated': size > OUTPUT_LIMIT,
                             'output_limit_bytes': OUTPUT_LIMIT})

    async def run(self, workspace, command, timeout, *, readonly, expected_files):
        if (workspace.root != Path('/app') or readonly or self.settings.read_only
                or self.settings.write_scope is not None):
            return await original_run(self, workspace, command, timeout,
                                      readonly=readonly, expected_files=expected_files)
        if conflict := self._check_expected(workspace, expected_files):
            return conflict
        before = workspace.snapshot()
        result = await asyncio.to_thread(command_in_container, self, command, workspace.root, timeout)
        result.changed_paths = Workspace.changes(before, workspace.snapshot())
        result.partial_success = bool(result.changed_paths) and result.status != 'success'
        return result

    SandboxRunner._run = run
    SandboxRunner.capabilities = lambda self: (
        'Diagnostic benchmark environment: run_shell executes at /app in a dedicated '
        'Docker container with no external network or host credentials. Background processes '
        'survive successful shell calls until this task container is removed after grading. '
        'run_tests still uses temporary workspace copies with Landlock/seccomp isolation. '
        'Verify service behavior as well as files.')
    model, description, effect = schemas.SCHEMAS['run_shell']
    schemas.SCHEMAS['run_shell'] = (model, description.replace(
        'Run a bounded sandbox command; workspace metadata such as .git is excluded.',
        'Run a bounded command in the dedicated task Docker container at /app.').replace(
        'Background processes do not survive tool calls; start servers, checks and cleanup in one bounded command.',
        'Background processes survive successful run_shell calls and are removed with the task container.'), effect)


async def preflight():
    import execution_environment

    from aster.config import Settings
    from aster.tools.testing import run_tests

    interpreter = os.environ['BENCH_TEST_PYTHON']
    execution_environment.install({
        'test_python': interpreter, 'dependency_root': str(Path(interpreter).parent.parent),
        'execution_env': {'PYTHONPATH': '{workspace}'}})
    mode = os.environ['BENCH_RETRY_MODE']
    install(mode)
    sandbox = SandboxRunner(Settings(sandbox='required', network=True))
    workspace = Workspace(Path(os.environ['BENCH_WORKSPACE']))
    checks = []
    try:
        if mode == 'pytest':
            check = await run_tests(workspace, sandbox,
                                    'python -m pytest testing/test_mark.py::TestMark -q', 120)
            checks.append(check.model_dump())
            assert check.status == 'success' and check.data['passed_tests'] > 0, check
        elif mode == 'grpc':
            check = await sandbox.run(workspace,
                'python -m http.server 54329 --bind 127.0.0.1 > .tmp/preflight.log 2>&1 & '
                'echo $! > .tmp/preflight.pid', 10)
            checks.append(check.model_dump())
            assert check.status == 'success', check
            check = await sandbox.run(workspace, "python -c 'import time, urllib.request; "
                "time.sleep(0.5); assert urllib.request.urlopen(\"http://127.0.0.1:54329\").status == 200'", 10)
            checks.append(check.model_dump())
            assert check.status == 'success', check
            check = await run_tests(workspace, sandbox, 'python -m pytest', 30,
                                    code='def test_environment():\n    assert 2 + 3 == 5\n')
            checks.append(check.model_dump())
            assert check.status == 'success' and check.data['passed_tests'] == 1, check
        return {'ready': True, 'mode': mode, 'checks': checks}
    finally:
        sandbox.close()


if __name__ == '__main__':
    import json
    print(json.dumps(asyncio.run(preflight())))
