"""Two frozen Aster rounds; prepare/verify/results are offline, run requires user confirmation."""
import argparse
import ast
import concurrent.futures
import fcntl
import hashlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
from pathlib import Path

from metrics import measure
from turn_metrics import measure as measure_turns

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
PRIVATE = REPO / 'artifacts/private' / HERE.name
BASE_HERE = HERE.parent / 'general-coding-50-20261012'
BASE = REPO / 'artifacts/private' / BASE_HERE.name
PYTHON = '/home/dzh/.conda/envs/aster/bin/python'
TAGS = {1: 'v11-stage2', 2: 'v11-stage3'}
STABLE_PASS = ['django__django-17029', 'sympy__sympy-23262', 'sphinx-doc__sphinx-9230',
               'pydata__xarray-7233', 'pydata__xarray-3677', 'pytest-dev__pytest-7571',
               'psf__requests-2931', 'terminal-polyglot-c-py', 'aider-simple-linked-list', 'aider-sgf-parsing']
STABLE_FAIL = ['django__django-11400', 'sympy__sympy-17630', 'sphinx-doc__sphinx-11510', 'pytest-dev__pytest-10356']
VARIABLE = ['terminal-cancel-async-tasks', 'terminal-custom-memory-heap-crash',
            'terminal-schemelike-metacircular-eval', 'terminal-kv-store-grpc',
            'terminal-cobol-modernization', 'psf__requests-5414']


def imported(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sys.path.insert(0, str(BASE_HERE))
engine = imported('turn_reduction_general50', BASE_HERE / 'evaluate.py')
load, save, hashes = engine.load, engine.save, engine.hashes


def paths(round_no, attempt):
    return (HERE / f'round{round_no}' / f'attempt{attempt}',
            PRIVATE / f'round{round_no}' / f'attempt{attempt}')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_base():
    manifest = load(BASE_HERE / 'manifest.json')
    assert load(BASE_HERE / 'environment-preflight.json')['ready']
    assert hashes(BASE / 'runtime') == manifest['runtime_hashes']
    changed = {}
    for name, expected in manifest['input_hashes'].items():
        actual = sha(REPO / name)
        if actual == expected:
            continue
        # Stage 0 split a dummy literal to meet the secret scan; the executed copy is unchanged.
        assert name == 'evaluations/general-coding-50-20261012/aster_episode.py', name
        original = BASE / 'runtime/aster_episode.py'
        assert sha(original) == expected
        assert ast.dump(ast.parse(original.read_text())) == ast.dump(ast.parse((REPO / name).read_text()))
        changed[name] = {'original': expected, 'current': actual, 'identical_ast': True,
                         'executed_original': str(original.relative_to(REPO))}
    for name, expected in manifest['external_binary_hashes'].items():
        assert sha(Path(name)) == expected, name
    return manifest, changed


def verify(round_no):
    manifest = load(HERE / f'manifest-round{round_no}.json')
    root = PRIVATE / f'round{round_no}'
    assert subprocess.check_output(['git', 'rev-parse', TAGS[round_no]], cwd=REPO, text=True).strip() == manifest['aster_commit']
    assert hashes(root / 'runtime') == manifest['runtime_hashes'], 'Frozen runtime changed'
    assert hashes(root / 'runtime/aster') == manifest['product_hashes'], 'Frozen source changed'
    for name, expected in manifest['input_hashes'].items():
        assert sha(REPO / name) == expected, name
    for name, expected in manifest['external_binary_hashes'].items():
        assert sha(Path(name)) == expected, name
    return manifest


def prepare(round_no):
    assert not (HERE / f'manifest-round{round_no}.json').exists(), 'Already frozen'
    base, changed = verify_base()
    root = PRIVATE / f'round{round_no}'
    assert not root.exists(), 'Snapshot directory already exists; inspect before proceeding'
    selected = [t for t in load(BASE / 'tasks.json') if t['exposure'] == 'old20']
    assert len(selected) == 20 and {t['id'] for t in selected} == set(STABLE_PASS + STABLE_FAIL + VARIABLE)
    shutil.copytree(BASE / 'runtime', root / 'runtime', ignore=shutil.ignore_patterns('aster', '__pycache__'))
    archive = subprocess.check_output(['git', 'archive', TAGS[round_no], 'src/aster'], cwd=REPO)
    with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
        for item in bundle:
            if item.isfile():
                target = root / 'runtime/aster' / Path(item.name).relative_to('src/aster')
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(bundle.extractfile(item).read())
    for name in ('sources', 'preflight', 'task-python', 'pi', 'node-v22.22.0-linux-x64'):
        (root / name).symlink_to((BASE / name).resolve(), target_is_directory=True)
    shutil.copy2(BASE / 'repository-dataset.json', root / 'repository-dataset.json')
    selected.sort(key=lambda t: hashlib.sha256(('schedule:' + t['id']).encode()).hexdigest())
    save(root / 'tasks.json', selected)
    files = [*HERE.glob('*.py'), HERE / 'PROTOCOL.md', HERE / 'baseline-turns.json',
             REPO / 'docs/turn-reduction-plan.md', BASE_HERE / 'manifest.json',
             BASE_HERE / 'results.json', HERE.parent / 'context-state-20261014/results.json',
             HERE.parent / 'context-state-20261014/metrics.json',
             HERE.parent / 'cache-probe-20261014/action_audit.py',
             HERE.parent / 'context-cache-20261013/metrics.py',
             root / 'tasks.json', root / 'repository-dataset.json']
    inputs = {name: sha(REPO / name) for name in base['input_hashes']}
    inputs.update({str(p.relative_to(REPO)): sha(p) for p in files})
    save(HERE / f'manifest-round{round_no}.json', {
        'aster_commit': subprocess.check_output(['git', 'rev-parse', TAGS[round_no]], cwd=REPO, text=True).strip(),
        'tag': TAGS[round_no], 'round': round_no, 'source_policy': 'Exact git tag exported with git archive',
        'model': 'gpt-5.6-luna', 'protocol': 'responses', 'reasoning': 'medium', 'seconds_per_task': 900,
        'context_tokens': 64000, 'output_tokens': 8192, 'max_workers': 0, 'automatic_memory': True,
        'cpu': 4, 'memory_gib': 8, 'concurrency': 6, 'planned_episodes': 32,
        'tasks': [t['id'] for t in selected], 'stable_pass': STABLE_PASS,
        'stable_fail': STABLE_FAIL, 'variable': VARIABLE,
        'product_hashes': hashes(root / 'runtime/aster'), 'runtime_hashes': hashes(root / 'runtime'),
        'input_hashes': inputs, 'external_binary_hashes': base['external_binary_hashes'],
        'historical_dummy_literal_normalization': changed,
    })
    verify(round_no)
    print(f'Round {round_no} frozen at {TAGS[round_no]}; no model requests made.')


def selection(manifest, attempt, tasks):
    selected = tasks if tasks is not None else manifest['tasks'] if attempt == 1 else VARIABLE
    assert selected and len(selected) == len(set(selected)), 'Empty or duplicate task selection'
    assert set(selected) <= set(manifest['tasks']), 'Unknown task'
    if attempt > 1:
        assert set(selected) <= set(VARIABLE + STABLE_PASS), 'Stable-fail tasks have only attempt 1'
    return [name for name in manifest['tasks'] if name in selected]


def configure(round_no, attempt):
    public, root = paths(round_no, attempt)
    engine.HERE, engine.PRIVATE = HERE, root
    engine.NETWORK = f'aster-{HERE.name}-r{round_no}-a{attempt}'
    engine.verify_frozen = lambda: verify(round_no)
    return public, root


def ensure_attempt(round_no, attempt):
    public, root = configure(round_no, attempt)
    root.mkdir(parents=True, exist_ok=True)
    for name in ('runtime', 'sources', 'preflight', 'task-python', 'pi', 'node-v22.22.0-linux-x64', 'tasks.json', 'repository-dataset.json'):
        target = root / name
        expected = root.parent / name
        if target.is_symlink():
            assert target.resolve() == expected.resolve()
        else:
            assert not target.exists()
            target.symlink_to(expected.resolve(), target_is_directory=expected.is_dir())
    (root / 'relay').mkdir(exist_ok=True)
    return public, root


def pending_tasks(root, names):
    by_id = {t['id']: t for t in load(root / 'tasks.json')}
    tasks = []
    for name in names:
        out = root / 'runs' / ('aster-' + name)
        if (out / 'launch.json').exists():
            assert (out / 'result.json').exists(), 'Classify incomplete attempt; never overwrite or rerun: ' + name
            continue
        assert not out.exists(), 'Unclassified raw directory: ' + name
        tasks.append(by_id[name])
    return tasks


def run(round_no, attempt, task_names):
    verify(round_no)
    with (PRIVATE / f"round{round_no}" / "run.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _run(round_no, attempt, task_names)


def _run(round_no, attempt, task_names):
    import docker

    manifest = verify(round_no)
    if round_no == 2:
        assert load(HERE / 'audit-round1.json')['all_gates_passed'], 'Round 1 must pass first'
    names = selection(manifest, attempt, task_names)
    if attempt > 1:
        first = load(paths(round_no, 1)[0] / 'results.json')
        assert len(first) == 20 and all(r['graded'] for r in first)
        failed = [r['task'] for r in first if r['task'] in STABLE_PASS and not r['passed']]
        rescue = set(names) - set(VARIABLE)
        assert not rescue or (len(failed) == 1 and rescue <= set(failed)), 'Only the one stable-pass failure may receive rescue attempts'
    public, root = ensure_attempt(round_no, attempt)
    tasks = pending_tasks(root, names)
    if not tasks:
        print('Selected attempts already completed; no model requests made.')
        return
    drv = engine.driver()
    os.chdir(root)  # SWE-bench writes logs relative to the controller's working directory.
    client = docker.from_env()
    for task in tasks:
        client.images.get(task['image_id'])
    socket = root / 'relay/model.sock'
    assert not socket.exists(), 'Stale gateway socket; inspect before running'
    network = client.networks.create(engine.NETWORK, internal=True)
    process = relay = None
    try:
        with (root / 'gateway.log').open('ab') as log:
            process = subprocess.Popen([PYTHON, str(HERE / 'evaluate.py'), 'gateway', '--round', str(round_no), '--attempt', str(attempt)],
                cwd=root, env={**os.environ, 'PYTHONPATH': str(root / 'runtime')}, stdout=log, stderr=log)
        for _ in range(100):
            if socket.exists():
                break
            assert process.poll() is None, 'Gateway exited'
            time.sleep(.1)
        assert socket.exists()
        relay = client.containers.run(tasks[0]['image_id'], [PYTHON, '/relay.py'], detach=True,
            name='aster-bench-gateway', network=engine.NETWORK,
            volumes={str(root / 'relay'): {'bind': '/relay', 'mode': 'rw'},
                     str(engine.baseline.ORIGINAL / 'relay.py'): {'bind': '/relay.py', 'mode': 'ro'},
                     str(Path(PYTHON).parent.parent): {'bind': str(Path(PYTHON).parent.parent), 'mode': 'ro'}})

        def one(task):
            row = drv.episode('aster', task)
            if task['kind'] == 'repository':
                patch = (root / 'runs' / row['job'] / 'prediction.patch').read_text()
                engine.repository_grade(task, 'aster', patch, f'{HERE.name}-r{round_no}-a{attempt}', skip_patch=not patch)
            return row

        helpers = imported('turn_reduction_usage', HERE.parent / 'general-coding-20261010/summarize.py')
        outages, iterator = [], iter(tasks)
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            pending = {pool.submit(one, next(iterator)) for _ in range(min(6, len(tasks)))}
            while pending:
                done, pending = concurrent.futures.wait(pending, return_when=concurrent.futures.FIRST_COMPLETED)
                for future in done:
                    row = future.result()
                    _, requests = helpers.usage(root / 'runs' / row['job'])
                    outages.append(bool(requests) and all(
                        r.get('error') in {'ReadTimeout', 'ConnectTimeout', 'ConnectError', 'RemoteProtocolError'}
                        or r.get('http_status', 200) >= 500 or r.get('http_status') == 429
                        or bool(r.get('stream_error')) for r in requests))
                if len(outages) >= 6 and all(outages[-6:]):
                    save(public / 'provider-outage.json', {'reason': 'six consecutive provider-only failures'})
                    raise RuntimeError('Provider outage; no more tasks submitted')
                for _ in done:
                    task = next(iterator, None)
                    if task is not None:
                        pending.add(pool.submit(one, task))
        verify(round_no)
    finally:
        if relay is not None:
            relay.remove(force=True)
        if process is not None:
            process.terminate()
            process.wait(timeout=10)
        socket.unlink(missing_ok=True)
        network.remove()
        client.close()


def results(round_no, attempt):
    verify(round_no)
    public, root = paths(round_no, attempt)
    helpers = imported('turn_reduction_summary', HERE.parent / 'general-coding-20261010/summarize.py')
    rows = []
    for task in load(root.parent / 'tasks.json'):
        out = root / 'runs' / ('aster-' + task['id'])
        if not (out / 'result.json').exists():
            continue
        row = load(out / 'result.json')
        usage, _ = helpers.usage(out)
        final, phase, stop = helpers.final_text(out, 'aster')
        row.update(usage, final=final, phase=phase, stop_reason=stop, exposure='old20', round=round_no, attempt=attempt)
        if task['kind'] == 'repository':
            path = root / 'logs/evaluation' / f'{HERE.name}-r{round_no}-a{attempt}' / 'aster' / task['id'] / 'report.json'
            grade = load(path).get(task['id']) if path.exists() else None
            row.update(grade=grade, graded=grade is not None, passed=bool(grade and grade.get('resolved')))
        else:
            row.update(graded='grade' in row, passed=bool(row.get('grade', {}).get('passed')))
        row['normal_delivery_and_passed'] = bool(row['passed'] and row['exit_code'] == 0 and final and phase == 'completed')
        rows.append(row)
    save(public / 'results.json', rows)
    save(public / 'metrics.json', measure(root / 'runs'))
    try:
        turns = measure_turns(root / 'runs')
    except ValueError as exc:
        turns = {'unavailable': str(exc)}
    save(public / 'turns.json', turns)
    print(json.dumps({'round': round_no, 'attempt': attempt, 'finished': len(rows),
                      'graded': sum(r['graded'] for r in rows), 'passed': sum(r['passed'] for r in rows)}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['prepare', 'verify', 'run', 'gateway', 'results'])
    parser.add_argument('--round', type=int, choices=[1, 2], required=True)
    parser.add_argument('--attempt', type=int, choices=[1, 2, 3], default=1)
    parser.add_argument('--tasks', nargs='+', help='Exact task IDs; default old20 for attempt 1, variable six for 2/3')
    args = parser.parse_args()
    if args.mode == 'prepare':
        prepare(args.round)
    elif args.mode == 'verify':
        verify(args.round)
        print('Frozen source and inputs verified; no model requests made.')
    elif args.mode == 'gateway':
        verify(args.round)
        configure(args.round, args.attempt)
        engine.gateway()
    elif args.mode == 'run':
        run(args.round, args.attempt, args.tasks)
    else:
        results(args.round, args.attempt)


if __name__ == '__main__':
    main()
