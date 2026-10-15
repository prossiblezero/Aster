"""Model-free checks of interpreter selection and temporary test isolation."""
import asyncio
import json
import os
from pathlib import Path

import execution_environment
import retry_environment
from aster.config import Settings
from aster.safety.runner import SandboxRunner
from aster.storage.workspace import Workspace
from aster.tools.testing import run_tests


async def main():
    mode = os.environ.get('BENCH_RETRY_MODE', '')
    if mode == 'grpc':
        return await retry_environment.preflight()
    interpreter = os.environ['BENCH_TEST_PYTHON']
    execution_environment.install({
        'test_python': interpreter,
        'dependency_root': str(Path(interpreter).parent.parent),
        'execution_env': {'PYTHONPATH': '{workspace}'},
    })
    retry_environment.install(mode)
    sandbox = SandboxRunner(Settings(sandbox='required', network=True))
    workspace = Workspace(Path(os.environ['BENCH_WORKSPACE']))
    checks = []
    try:
        # This is discarded with the preflight container, never an agent candidate.
        check = await sandbox.run(workspace, "python -c 'from pathlib import Path; "
            "Path(\"aster_probe.txt\").write_text(\"ok\")'", 30)
        checks.append(check.model_dump())
        assert check.status == 'success' and workspace.read('aster_probe.txt') == 'ok', check
        if mode == 'pytest':
            check = await run_tests(workspace, sandbox,
                                    'python -m pytest testing/test_mark.py::TestMark -q', 120)
            checks.append(check.model_dump())
            assert check.status == 'success' and check.data['passed_tests'] > 0, check
            code = '''def test_generated_project(testdir):
    testdir.makepyfile("import pytest\\n@pytest.mark.project_only\\ndef test_case(): pass\\n")
    testdir.runpytest("-q").assert_outcomes(passed=1)
'''
            check = await run_tests(workspace, sandbox, 'python -m pytest -q', 120, code=code)
        else:
            check = await run_tests(workspace, sandbox, 'python -m unittest -q', 60, code='''
import sys
import unittest
import django
class RuntimeChecks(unittest.TestCase):
    def test_task_interpreter(self):
        self.assertEqual(sys.version_info[:2], (3, 6))
        self.assertTrue(django.get_version())
''')
        checks.append(check.model_dump())
        assert check.status == 'success' and check.data['passed_tests'] == 1, check
        return {'ready': True, 'mode': mode or 'python36', 'checks': checks}
    finally:
        sandbox.close()


if __name__ == '__main__':
    print(json.dumps(asyncio.run(main())))
