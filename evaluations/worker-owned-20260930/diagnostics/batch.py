"""Freeze before paid runs; run supplementary diagnostics only after all 60 finish."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import fcntl
import hashlib
import json
from pathlib import Path

import public_examples as diagnostic

HERE, REPO = diagnostic.HERE, diagnostic.REPO
OUT = REPO / 'artifacts/private/worker-owned-20260930'
SELECTION = REPO / 'datasets/worker-owned-20260930/selection.json'
FREEZE = HERE / 'freeze.json'
KINDS = {'swe-verified-sympy__sympy-14248': 'printing', 'holdout-sympy__sympy-13878': 'cdf'}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')


def identity():
    preflight_path = OUT / 'diagnostic-preflight/preflight.json'
    preflight = json.loads(preflight_path.read_text())
    paths = [*HERE.glob('*.py'), HERE / 'PREFLIGHT.md', HERE.parent / 'SUPPLEMENTARY-PROTOCOL.md', SELECTION,
             diagnostic.LAUNCHER, diagnostic.PYTHON, preflight_path]
    paths += [REPO / value['path'] for value in [preflight['math_reference'], *preflight['runs'].values()]]
    return {'files': {str(path): sha(path) for path in sorted(paths)},
            'cases': {kind: diagnostic.cases(kind) for kind in ('printing', 'cdf')},
            'timeout_seconds_per_point': diagnostic.TIMEOUT}


def audit():
    frozen = json.loads(FREEZE.read_text())
    if frozen['identity'] != identity():
        raise ValueError('supplementary inputs changed after freeze')
    return frozen


def run():
    frozen = audit()
    design = json.loads(SELECTION.read_text())
    assert design['arms'] == ['solo', 'current', 'worker'] and design['repeats'] == 2 and len(design['tasks']) == 10
    expected = [(task['id'], repeat, arm) for task in design['tasks'] for repeat in (1, 2) for arm in design['arms']]
    # Require all completed result artifacts, including failures/stops, before any diagnostic.
    missing = [key for key in expected if not (OUT / f'repeat-{key[1]:02d}/runs/{key[0]}--{key[2]}/result.json').exists()]
    if missing:
        raise ValueError(f'{len(missing)} episodes unfinished; no diagnostics may overlap paid runs')
    rows = []
    for task, repeat, arm in expected:
        if task not in KINDS:
            continue
        root = OUT / f'repeat-{repeat:02d}'
        episode = root / f'runs/{task}--{arm}'
        workspace = root / f'workspaces/{task}--{arm}'
        path = OUT / f'diagnostics/repeat-{repeat:02d}--{KINDS[task]}--{arm}.json'
        if path.exists():
            report = json.loads(path.read_text())
            if report['freeze_sha256'] != sha(FREEZE) or report['result_sha256'] != sha(episode / 'result.json'):
                raise ValueError('saved diagnostic identity differs; preserve and investigate')
        else:
            log = episode / 'grading/tests.log'
            report = diagnostic.run(workspace, KINDS[task], grading_log=log if log.exists() else None)
            report.update(task_id=task, repeat=repeat, arm=arm, freeze_sha256=sha(FREEZE),
                          result_sha256=sha(episode / 'result.json'), frozen_at=frozen['frozen_at'])
            save(path, report)
        rows.append({'task': task, 'repeat': repeat, 'arm': arm, 'path': str(path.relative_to(REPO)),
                     'sha256': sha(path), 'summary': report['summary'], 'upstream_tests': report.get('upstream_tests', {})})
        print('diagnostic', repeat, task, arm, flush=True)
    summary = {'scope': 'independent supplementary columns; original benchmark scores unchanged',
               'freeze_sha256': sha(FREEZE), 'episodes': rows, 'counts': {}}
    for kind in ('printing', 'cdf'):
        for arm in design['arms']:
            chosen = [row for row in rows if KINDS[row['task']] == kind and row['arm'] == arm]
            counts = Counter(part['all']['status'] for row in chosen for part in row['summary'].values())
            summary['counts'][kind + '--' + arm] = dict(counts)
    save(OUT / 'diagnostics/summary.json', summary)
    audit()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('freeze', 'audit', 'run'))
    mode = parser.parse_args().mode
    OUT.mkdir(parents=True, exist_ok=True)
    # Same lock as the paid controller: no CPU or disk overlap with the model run.
    with (OUT / 'controller.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if mode == 'freeze':
            if FREEZE.exists():
                audit()
            else:
                if list(OUT.glob('repeat-*/runs/*/launch.json')):
                    raise ValueError('diagnostics must be frozen before the first paid launch')
                save(FREEZE, {'frozen_at': datetime.now(timezone.utc).isoformat(), 'identity': identity()})
        elif mode == 'audit':
            audit()
        else:
            run()
        print(mode, 'ok', flush=True)


if __name__ == '__main__':
    main()
