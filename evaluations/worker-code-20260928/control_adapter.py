"""Stage and score the two lightweight dataset adapters using immutable upstream tests."""
from __future__ import annotations

import asyncio
import gzip
import json
import shutil
import tempfile
from pathlib import Path

from aster.config import Settings
from aster.safety.runner import SandboxRunner
from aster.storage.workspace import Workspace
from aster.tools.testing import run_tests


def content(task, cache):
    g = task['grading']
    if g['adapter'] == 'aider_unittest':
        return {k: (cache / g[k]).read_text() for k in ['starter', 'reference', 'tests']} | {
            'filename': g['filename'], 'test_filename': g['test_filename']}
    with gzip.open(cache / g['record_archive'], 'rt') as f:
        row = next(r for line in f if (r := json.loads(line))['task_id'] == g['row_id'])
    return {'starter': row['code_prompt'] + '    pass\n',
            'reference': row['complete_prompt'] + row['canonical_solution'],
            'tests': 'from solution import task_func\n' + row['test'],
            'filename': 'solution.py', 'test_filename': 'test_solution.py'}


def prepare(task, cache: Path, workspace: Path) -> dict:
    workspace.mkdir(parents=True, exist_ok=False)
    data = content(task, cache)
    if task['grading']['adapter'] == 'aider_unittest':
        for f in task['materials']['public']:
            p = workspace / f['path']
            p.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(cache / f['asset'], p)
    else:
        (workspace / data['filename']).write_text(data['starter'])
    return {'ready': True, 'scoring_mode': 'upstream_unittest_immutable_replay',
            'prompt_suffix': 'Implement the requested interface in ' + data['filename'] +
                '. Use the provided public tests if present, and add focused local checks as needed. '
                'Run verification with run_tests using python -m unittest or pytest. '
                'The evaluator runs a separate immutable copy of the upstream tests.'}


def grade(task, cache: Path, workspace: Path, output: Path) -> dict:
    data = content(task, cache)
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='aster-worker-grade-') as folder:
        target = Path(folder)
        for src in Workspace(workspace).files():
            dst = target / src.relative_to(workspace)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
        (target / data['test_filename']).write_text(data['tests'])
        runner = SandboxRunner(Settings(sandbox='required', network=False))
        try:
            result = asyncio.run(run_tests(Workspace(target), runner,
                'python -m unittest discover -s . -p ' + data['test_filename'], task['grading']['timeout_seconds']))
        finally:
            runner.close()
    count = result.data.get('passed_tests', 0)
    passed = result.status == 'success' and count == task['grading']['expected_tests']
    row = {'passed': passed, 'status': result.status, 'reason': result.reason,
           'mode': 'upstream_unittest_immutable_replay', 'expected': task['grading']['expected_tests'],
           'passed_tests': count, 'output': result.content, 'isolation': result.data.get('isolation')}
    (output / 'grade.json').write_text(json.dumps(row, indent=2) + '\n')
    return row


def preflight(task, cache: Path, root: Path) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    results = {}
    for arm in ['starter', 'reference']:
        work = root / arm
        prepare(task, cache, work)
        if arm == 'reference':
            data = content(task, cache)
            (work / data['filename']).write_text(data['reference'])
        results[arm] = grade(task, cache, work, root / (arm + '-grade'))
    return {'id': task['id'], 'ready': not results['starter']['passed'] and results['reference']['passed'],
            'mode': 'upstream_unittest_immutable_replay', **results}
