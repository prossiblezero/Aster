"""No-model path checks in the actual task image and interpreter."""
import asyncio
import json
import os
from pathlib import Path

import execution_environment

from aster.config import Settings
from aster.safety.runner import SandboxRunner
from aster.storage.workspace import Workspace
from aster.tools.testing import run_tests


async def main():
    interpreter = os.environ['BENCH_TEST_PYTHON']
    execution_environment.install({
        'test_python': interpreter,
        'dependency_root': str(Path(interpreter).parent.parent),
        'execution_env': {'PYTHONPATH': '{workspace}'},
    })
    workspace = Workspace(Path('/app'))
    before = workspace.snapshot()
    sandbox = SandboxRunner(Settings(sandbox='required', network=True))
    checks = []
    try:
        for command in (
            "cd /app && printf '(+ 7 8)\\n' | python interp.py test/calculator.scm",
            "printf '(+ 7 8)\\n' | python /app/interp.py /app/test/calculator.scm",
        ):
            result = await sandbox.run(workspace, command)
            assert result.status == 'success', result.model_dump()
            assert result.content.splitlines() == ['Reading', '15', 'Done'], result.model_dump()
            checks.append({'command': command, **result.model_dump()})
        result = await run_tests(workspace, sandbox, 'python -m pytest', 30, code=(
            'from pathlib import Path\n'
            'def test_absolute_project_paths():\n'
            "    assert Path.cwd() == Path('/app')\n"
            "    assert Path('/app/interp.py').is_file()\n"
        ))
        assert result.status == 'success' and result.data['passed_tests'] == 1, result.model_dump()
        checks.append(result.model_dump())
        assert workspace.snapshot() == before
        print(json.dumps({'ready': True, 'checks': checks}))
    finally:
        sandbox.close()


if __name__ == '__main__':
    asyncio.run(main())
