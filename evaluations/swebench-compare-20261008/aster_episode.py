"""Run the unchanged Aster runtime inside one benchmark container."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import execution_environment

from aster.config import Settings
from aster.core.runtime import Runtime
from aster.schema import TaskSpec


async def main():
    execution_environment.install({
        "test_python": "/opt/miniconda3/envs/testbed/bin/python",
        "dependency_root": "/opt/miniconda3/envs/testbed",
        "execution_env": {"PYTHONPATH": "{workspace}", "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1"},
    })
    settings = Settings(model="gpt-5.6-luna", base_url=os.environ["BENCH_BASE_URL"], api_key="benchmark",
                        stream=True, context_tokens=64000, output_tokens=8192, request_timeout=120,
                        approval="auto", sandbox="required", network=False, completion="strict", max_workers=0)
    # The whole disposable container is authorized for unattended benchmark work.
    # Explicit policy denials and Landlock restrictions still apply.
    runtime = Runtime(Path("/testbed"), settings, approval_callback=lambda _call, _decision: True)
    try:
        await runtime.run(TaskSpec(prompt=Path("/agent-state/prompt.txt").read_text(), max_steps=500,
                                   max_turns=500, require_edit=True, require_verification=True))
        await runtime.maintenance.drain(timeout=15)
    finally:
        await runtime.maintenance.close()
        await runtime.workers.close()
        runtime.sandbox.close()
        Path("/agent-state/aster-result.json").write_text(runtime.state.model_dump_json(indent=2))
    print(json.dumps({"phase": runtime.state.phase, "stop_reason": runtime.state.stop_reason,
                      "final": runtime.state.final, "usage": runtime.state.usage}), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
