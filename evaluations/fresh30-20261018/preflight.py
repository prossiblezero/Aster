"""Starter must fail and reference must pass for every fresh30 task; no model calls.

Reuses general-50's preflight on the fresh pool, with repository grading on the tarpit
network used by every evaluation run. Writes preflight.json and the final tasks.json:
all ready non-terminal tasks plus the first two ready terminal candidates in rank order.
"""
import importlib.util
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
PRIVATE = REPO / 'artifacts/private' / HERE.name
V12 = HERE.parent / 'harness-v12-20261017'


def imported(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    sys.path.insert(0, str(V12))
    harness = imported('fresh30_harness', V12 / 'evaluate.py')
    engine = harness.engine
    tasks = json.loads((PRIVATE / 'tasks.provisioned.json').read_text())
    shutil.copy2(PRIVATE / 'tasks.provisioned.json', PRIVATE / 'tasks.json')
    original_grade = engine.repository_grade

    def grade(task, agent, patch, run_id, skip_patch=False):
        harness.engine.repository_grade = original_grade
        try:
            return harness.tarpit_grade(task, agent, patch, run_id, skip_patch=skip_patch)
        finally:
            harness.engine.repository_grade = grade

    engine.PRIVATE, engine.HERE, engine.NETWORK = PRIVATE, HERE, 'aster-fresh30-preflight'
    engine.repository_grade = grade
    try:
        engine.preflight()
    except AssertionError:
        pass  # general-50 asserts exactly 50 tasks in its summary; we summarize below
    results = {t['id']: json.loads((PRIVATE / 'preflight' / t['id'] / 'preflight.json').read_text())
               for t in tasks if (PRIVATE / 'preflight' / t['id'] / 'preflight.json').exists()}
    final, terminals = [], []
    for task in tasks:
        ready = results.get(task['id'], {}).get('ready')
        if task.get('terminal_candidate'):
            if ready and len(terminals) < 2:
                terminals.append(task)
                final.append(task)
        elif ready:
            final.append(task)
    summary = {'ready': {k: v.get('ready') for k, v in results.items()},
               'selected': [t['id'] for t in final], 'count': len(final)}
    (HERE / 'preflight.json').write_text(json.dumps(summary, indent=2) + '\n')
    (PRIVATE / 'tasks.json').write_text(json.dumps(final, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
