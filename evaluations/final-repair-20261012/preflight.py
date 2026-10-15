"""Exercise real absolute paths and formal tests without calling a model."""
import asyncio
import json
import os
import shlex
from pathlib import Path

import execution_environment
import retry_environment

from aster.config import Settings
from aster.safety.runner import SandboxRunner
from aster.storage.workspace import Workspace
from aster.tools.testing import run_tests


async def main():
    interpreter = os.environ['BENCH_TEST_PYTHON']
    execution_environment.install({'test_python': interpreter,
        'dependency_root': str(Path(interpreter).parent.parent),
        'execution_env': {'PYTHONPATH': '{workspace}'}})
    retry_environment.install(os.environ.get('BENCH_RETRY_MODE', ''))
    workspace = Workspace(Path(os.environ['BENCH_WORKSPACE']))
    sandbox = SandboxRunner(Settings(sandbox='required', network=True))
    try:
        result = await sandbox.run(workspace, 'cd ' + str(workspace.root) + ' && pwd', 30)
        assert result.status == 'success' and result.content.strip() == str(workspace.root), result
        code = ('import unittest\nfrom pathlib import Path\n'
                'class PathCheck(unittest.TestCase):\n'
                '    def test_path(self):\n'
                '        self.assertEqual(str(Path.cwd()), ' + repr(str(workspace.root)) + ')\n')
        test = await run_tests(workspace, sandbox, 'python -m unittest -q', 30, code=code)
        assert test.status == 'success' and test.data['passed_tests'] == 1, test
        checks = {'ready': True, 'path': result.model_dump(), 'test': test.model_dump()}
        if os.environ['BENCH_PREFLIGHT_TASK'] == 'django__django-17029':
            command = 'python tests/runtests.py --settings=test_sqlite --parallel 1 apps'
            prefixed = 'cd ' + shlex.quote(str(workspace.root)) + ' && ' + command
            root_test = await run_tests(workspace, sandbox, prefixed, 120)
            if os.environ['BENCH_PREFLIGHT_PHASE'].endswith('baseline'):
                assert root_test.reason == 'test_command_not_supported', root_test
                root_test = await run_tests(workspace, sandbox, command, 120)
            assert root_test.status == 'success' and root_test.data['passed_tests'] > 0, root_test
            checks['native_project_tests'] = root_test.model_dump()
        print(json.dumps(checks))
    finally:
        sandbox.close()


if __name__ == '__main__':
    asyncio.run(main())
