"""Unchanged production Runtime in the task's isolated container."""
import asyncio
import json
import os
from pathlib import Path

import execution_environment

from aster.config import Settings
from aster.core.runtime import Runtime
from aster.schema import TaskSpec


async def main():
    workspace = Path(os.environ['BENCH_WORKSPACE'])
    interpreter = os.environ['BENCH_TEST_PYTHON']
    execution_environment.install({
        'test_python': interpreter, 'extra_path': ['/opt/node/bin'],
        'dependency_root': str(Path(interpreter).parent.parent),
        'execution_env': {'PYTHONPATH': '{workspace}', 'OPENBLAS_NUM_THREADS': '1',
                          'OMP_NUM_THREADS': '1', 'MPLBACKEND': 'Agg', 'NODE_PATH': '/opt/task-python/js/node_modules'},
    })
    settings = Settings(model='gpt-5.6-luna', base_url=os.environ['BENCH_BASE_URL'],
                        api_key='benchm' 'ark', stream=True, context_tokens=64000,
                        output_tokens=8192, request_timeout=120, approval='auto',
                        sandbox='required', network=True, completion='strict', max_workers=0,
                        # Same as the product's one-shot CLI where the version has the setting.
                        **({'background_maintenance': False}
                           if 'background_maintenance' in Settings.model_fields else {}))
    runtime = Runtime(workspace, settings, approval_callback=lambda _call, _decision: True)
    try:
        await runtime.run(TaskSpec(prompt=Path('/agent-state/prompt.txt').read_text(),
                                   max_steps=500, max_turns=500,
                                   require_edit=True, require_verification=True))
        # Same as the product's one-shot CLI: versions without the setting drained 15 s.
        wait = getattr(runtime.settings, 'oneshot_maintenance_seconds', 15)
        if wait:
            await runtime.maintenance.drain(timeout=wait)
    finally:
        await runtime.maintenance.close()
        await runtime.workers.close()
        runtime.sandbox.close()
        if runtime.state:
            Path('/agent-state/aster-result.json').write_text(runtime.state.model_dump_json(indent=2))
    print(json.dumps({'phase': runtime.state.phase, 'stop_reason': runtime.state.stop_reason,
                      'final': runtime.state.final, 'usage': runtime.state.usage}), flush=True)


if __name__ == '__main__':
    asyncio.run(main())
