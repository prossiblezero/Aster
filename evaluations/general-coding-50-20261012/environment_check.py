"""Model-free smoke check of Aster's adapter in each task interpreter."""
import asyncio
import json
import os
from pathlib import Path

import execution_environment

from aster.config import Settings
from aster.safety.runner import SandboxRunner
from aster.storage.workspace import Workspace


async def main():
    interpreter = os.environ['BENCH_TEST_PYTHON']
    execution_environment.install({'test_python': interpreter, 'extra_path': ['/opt/node/bin'],
        'dependency_root': str(Path(interpreter).parent.parent),
        'execution_env': {'PYTHONPATH': '{workspace}', 'OPENBLAS_NUM_THREADS': '1', 'OMP_NUM_THREADS': '1',
                          'MPLBACKEND': 'Agg', 'NODE_PATH': '/opt/task-python/js/node_modules'}})
    workspace = Workspace(Path(os.environ['BENCH_WORKSPACE']))
    sandbox = SandboxRunner(Settings(sandbox='required', network=True))
    command = "python -c 'import sys; open(\"adapter_probe.txt\",\"w\").write(\"ok\"); print(sys.version)'"
    if os.environ['BENCH_LANGUAGE'] == 'JavaScript':
        command += ' && /opt/node/bin/node -e "console.log(require(\'jest/package.json\').version)"'
    elif os.environ['BENCH_LANGUAGE'] == 'C++':
        command += ' && g++ --version'
    if os.environ.get('BENCH_IMPORT'):
        command += ' && python -c "import ' + os.environ['BENCH_IMPORT'] + '"'
    try:
        result = await sandbox.run(workspace, command, 120)
        assert result.status == 'success' and workspace.read('adapter_probe.txt') == 'ok', result
        print(json.dumps({'ready': True, 'result': result.model_dump()}))
    finally:
        sandbox.close()


if __name__ == '__main__':
    asyncio.run(main())
