"""One bounded live handoff check on new fixtures, not a speed/quality benchmark."""
from __future__ import annotations

import ast
import asyncio
import hashlib
import json
import time
from collections import Counter
from pathlib import Path

from aster.config import load_settings
from aster.core.runtime import Runtime
from aster.schema import TaskSpec, ToolCall

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
OUT = REPO / "artifacts/private/subagent-handoff-20261002/live-readonly"
FILES = {
    "pricing.py": '# Prices are integer units.\n\ndef total(items, fee=2):\n'
    '    subtotal = sum(price * quantity for price, quantity in items)\n'
    '    if subtotal >= 20:\n        fee = 0\n    return subtotal + fee\n',
    "window.py": '# Events retain their input order.\n\ndef select(events, lower, upper):\n'
    '    return [value for when, value in events if lower <= when < upper]\n',
}
QUESTIONS = {
    "pricing.py": {"p1": "total([(3, 2), (5, 1)])", "p2": "total([(5, 4)])"},
    "window.py": {"w1": "select([(2, 'a'), (4, 'b'), (5, 'c')], 2, 5)",
                  "w2": "select([(2, 'a'), (4, 'b')], 4, 4)"},
}
EXPECTED = {"p1": 13, "p2": 20, "w1": ["a", "b"], "w2": []}
PROMPT = (
    'Investigate the supplied Python files read-only. Do not modify files. Each question is independent. '
    'Return only JSON {"answers":[{"id":"question id","value":<result>, '
    '"evidence":{"path":"file.py","symbol":"function_name","line":<line within that function>}}]}. '
    'Cover each ID exactly once. Two workers are assigned by the harness; integrate their reports '
    'and verify any specific gaps. Questions: ' + json.dumps(QUESTIONS)
)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def main():
    OUT.mkdir(parents=True, exist_ok=False)
    root = OUT / "workspace"
    root.mkdir()
    for name, content in FILES.items():
        (root / name).write_text(content)
    config = load_settings(
        REPO / ".env", context_tokens=32000, output_tokens=8192, stream=True,
        request_timeout=90, approval="auto", read_only=True, completion="strict",
        max_workers=0, memory_enabled=False, network=False,
    )
    parent = Runtime(root, config)
    parent.start(TaskSpec(prompt=PROMPT, require_observation=True, max_steps=24, max_turns=12, max_tokens=100000))
    assert parent.state.task.require_observation and not parent.state.task.require_edit
    assert not parent.state.task.require_verification
    frozen = {"source": {str(p.relative_to(REPO)): digest(p) for p in sorted((REPO / "src").rglob("*.py"))},
              "driver": digest(Path(__file__)), "settings": config.identity(),
              "endpoint_sha256": hashlib.sha256(config.base_url.encode()).hexdigest(),
              "fixtures": {name: digest(root / name) for name in FILES},
              "prompt": PROMPT, "expected": EXPECTED,
              "effective_task": parent.state.task.model_dump(),
              "limits": {"max_steps": 24, "max_turns": 12, "max_tokens": 100000, "seconds": 180}}
    (HERE / "SMOKE-FREEZE.json").write_text(json.dumps(frozen, ensure_ascii=False, indent=2) + "\n")
    started, error = time.monotonic(), None

    async def run():
        ids = []
        for name, questions in QUESTIONS.items():
            assignment = "Read " + name + " and answer only these questions: " + json.dumps(questions)
            result = await parent.workers.start(assignment, explicit=True, parent_work="Integrate the two reports into the requested JSON.")
            assert result["status"] == "running", result["status"]
            ids.append(result["worker_id"])
        await parent.step(ToolCall(name="worker_status", arguments={
            "worker_id": ids, "wait_seconds": 160, "wait_for": "all",
        }))
        if any(worker.runtime.state.phase != "completed" for worker in parent.workers.workers.values()):
            raise RuntimeError("child_did_not_complete")
        while not parent.state.terminal:
            await parent.turn()

    try:
        await asyncio.wait_for(run(), timeout=180)
    except Exception as exc:
        error = type(exc).__name__
    finally:
        await parent.workers.close()
        parent.sandbox.close()
    states = [parent.state, *(worker.runtime.state for worker in parent.workers.workers.values())]
    events = [row for state in states for row in parent.evidence.events(state.run_id)]
    usage = Counter()
    for state in states:
        usage.update(state.usage)
    finishes = [row for row in events if row["event"] == "tool_result" and row["call"]["name"] == "worker_finish"]
    references = [source["evidence_id"] for row in finishes for source in row["call"]["arguments"].get("sources", [])]
    checks = {}
    try:
        answers = json.loads(parent.state.final)["answers"]
        assert len(answers) == len(EXPECTED) and {row["id"] for row in answers} == set(EXPECTED)
        for answer in answers:
            evidence = answer["evidence"]
            node = next(node for node in ast.walk(ast.parse(FILES[evidence["path"]]))
                        if isinstance(node, ast.FunctionDef) and node.name == evidence["symbol"])
            checks[answer["id"]] = (answer["value"] == EXPECTED[answer["id"]]
                                      and answer["id"] in QUESTIONS[evidence["path"]]
                                      and node.lineno <= evidence["line"] <= node.end_lineno)
    except (ValueError, KeyError, TypeError, AssertionError, StopIteration):
        checks["format"] = False
    source_unchanged = all(digest(REPO / path) == sha for path, sha in frozen["source"].items())
    fixture_unchanged = all(digest(root / path) == sha for path, sha in frozen["fixtures"].items())
    result = {"scope": "one live integration smoke; no baseline, no autonomous delegation or speed claim",
              "passed": error is None and parent.state.phase == "completed" and all(checks.values())
              and source_unchanged and fixture_unchanged,
              "seconds": time.monotonic() - started, "error_type": error, "checks": checks,
              "source_unchanged": source_unchanged, "fixture_unchanged": fixture_unchanged,
              "reported_usage": usage, "team_usage": parent.state.team_usage,
              "worker_finish_attempts": len(finishes),
              "rejection_reasons": dict(Counter(row["outcome"]["reason"] for row in finishes if row["outcome"]["status"] != "success")),
              "reference_lengths": dict(Counter(map(len, references))),
              "parent_read_calls": sum(row["event"] == "tool_result" and row["call"]["name"] == "read_file"
                                       for row in parent.evidence.events(parent.state.run_id)),
              "runs": [{"run_id": state.run_id, "phase": state.phase, "stop_reason": state.stop_reason,
                        "turns": state.turn, "usage": state.usage} for state in states],
              "trace_sha256": {str(path.relative_to(OUT)): digest(path) for path in sorted(root.glob('.aster/runs/*/trace.jsonl'))},
              "final": parent.state.final}
    (HERE / "smoke.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({key: value for key, value in result.items() if key not in {"runs", "trace_sha256", "final"}}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
