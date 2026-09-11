"""Reproducible deterministic Harness evaluation; no remote model required.

The report deliberately separates boundary correctness from model task success.
Use ``--real-model`` only when the configured gateway should be measured.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
from pathlib import Path

from aster.config import Settings
from aster.context import ContextBuilder
from aster.memory import Memory
from aster.rl import AsterEnv
from aster.runtime import Runtime
from aster.schema import ModelReply, RunState, TaskSpec, ToolCall
from aster.workspace import BoundaryError, Workspace


class FakeModel:
    def __init__(self, replies):
        self.replies = iter(replies)

    async def complete(self, _messages, _tools):
        return next(self.replies)


def reply(*calls, text=""):
    return ModelReply(
        text=text, calls=list(calls), raw=[{"role": "assistant", "content": text}] if text else []
    )


async def harness_cases() -> list[dict]:
    rows = []
    root = Path(tempfile.mkdtemp(prefix="aster-eval-"))
    try:
        for raw in ["../x", ".git/config", ".aster/trace", ".env", "secret.pem"]:
            try:
                Workspace(root).path(raw)
                rows.append({"case": "path_boundary:" + raw, "passed": False})
            except BoundaryError:
                rows.append({"case": "path_boundary:" + raw, "passed": True})
        (root / "app.py").write_text("print('old')\n")
        (root / "test_app.py").write_text("def test_ok():\n    assert True\n")
        model = FakeModel(
            [
                reply(ToolCall(name="read_file", arguments={"path": "app.py"}, call_id="r")),
                reply(
                    ToolCall(
                        name="patch_file",
                        arguments={"path": "app.py", "old_text": "old", "new_text": "new"},
                        call_id="p",
                    )
                ),
                reply(
                    ToolCall(
                        name="run_tests",
                        arguments={"command": "python -m pytest -q", "timeout": 20},
                        call_id="t",
                    )
                ),
                reply(text="completed"),
            ]
        )
        runtime = Runtime(
            root, Settings(approval="auto", sandbox="required", completion="strict"), model=model
        )
        state = await runtime.run(TaskSpec(prompt="Modify app.py and run tests", max_steps=8))
        rows.append(
            {
                "case": "production_loop_edit_verify",
                "passed": state.phase == "completed" and state.verification.state == "passed",
            }
        )

        env = AsterEnv(root, Settings(approval="never", sandbox="required"))
        env.reset(TaskSpec(prompt="edit app.py", max_steps=2))
        _obs, reward, _term, _trunc, info = await env.step(
            ToolCall(
                name="write_file", arguments={"path": "app.py", "content": "unsafe"}, call_id="rl"
            )
        )
        rows.append(
            {
                "case": "rl_shares_permission_boundary",
                "passed": reward < 0 and info["outcome"]["status"] == "blocked",
            }
        )

        builder = ContextBuilder(300, 100)
        state = RunState(task=TaskSpec(prompt="context"))
        state.messages = [{"role": "tool", "content": "x" * 700} for _ in range(10)]
        _prompt, report = builder.build(state, "context")
        rows.append(
            {
                "case": "context_compact",
                "passed": report.compacted and report.estimated_tokens <= report.budget,
            }
        )

        memory = Memory(root)
        accepted = memory.record(
            "run tests after changes", run_id="eval", evidence_id="e1", tags=["tests"]
        )
        rejected = memory.record("must reject", run_id="eval", evidence_id="", tags=[])
        snap = memory.snapshot()
        memory.record("temporary", run_id="eval", evidence_id="e2")
        rolled = memory.rollback(snap)
        rows.append(
            {
                "case": "memory_evolution_provenance_rollback",
                "passed": bool(accepted)
                and rejected is None
                and rolled
                and not any(x["text"] == "temporary" for x in memory.state["lessons"]),
            }
        )

        # RL accepts the wire-format discriminator and still uses the same boundary.
        env = AsterEnv(root, Settings(approval="never", sandbox="required"))
        env.reset(TaskSpec(prompt="inspect", max_steps=2))
        _obs, _reward, _term, _trunc, info = await env.step(
            {"kind": "tool", "name": "list_files", "arguments": {}}
        )
        rows.append({"case": "rl_wire_action", "passed": info["outcome"]["status"] == "success"})
    finally:
        shutil.rmtree(root, ignore_errors=True)
    return rows


async def main():
    rows = await harness_cases()
    report = {
        "suite": "aster-deterministic-harness-v1",
        "cases": rows,
        "passed": sum(x["passed"] for x in rows),
        "total": len(rows),
        "pass_rate": sum(x["passed"] for x in rows) / len(rows),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    Path("artifacts").mkdir(exist_ok=True)
    Path("artifacts/deterministic-evaluation.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    )
    Path("artifacts/deterministic-evaluation.md").write_text(
        "# Deterministic Harness evaluation\n\n"
        + f"- Cases: {report['total']}\n- Passed: {report['passed']}\n- Pass rate: {report['pass_rate']:.0%}\n\n"
        + "\n".join(f"- {'PASS' if r['passed'] else 'FAIL'} `{r['case']}`" for r in rows)
        + "\n"
    )


if __name__ == "__main__":
    asyncio.run(main())
