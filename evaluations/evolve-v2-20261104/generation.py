"""At most two generations: propose A/B, run a simultaneous seed/A/B triplet, decide.

  generation.py N --base TAG --base-runs RUN...

The execution helper is also used for the initial paired noise runs and final heldout
pairs. Finished artifacts are reused; interrupted model episodes are never resampled.
"""
import argparse
import concurrent.futures
import fcntl
import json
import subprocess
import sys
import threading
import time

import analyze
from common import CONTROLLER, DEV_POOL, HERE, MODEL_ARGS, PRIVATE, PUBLIC, REPO, SEED, V12, WORK, dev_tasks, load, save

EVALUATE = V12 / 'evaluate.py'


def sh(*args, log=None):
    with open(log, 'a') if log else open('/dev/null', 'w') as out:
        return subprocess.run([str(a) for a in args], cwd=REPO, stdout=out, stderr=subprocess.STDOUT).returncode


def propose(name, base, runs, sibling=None):
    report = WORK / name / 'report.json'
    if report.exists():
        result = load(report)
        assert result['base'] == base and result['runs'] == runs
        return result
    assert not (WORK / name).exists(), f'{name}: interrupted proposal needs inspection; do not resample'
    args = [sys.executable, HERE / 'propose.py', name, '--base', base, '--runs', *runs]
    if sibling:
        args += ['--sibling', sibling]
    code = sh(*args, log=WORK / f'{name}.log')
    assert report.exists(), f'Proposal wrapper exited {code}; inspect {name} before continuing'
    return load(report)


def prepare(runs, *, concurrency=2, pool=DEV_POOL, tasks=None):
    assert 0 < len(runs) * concurrency <= 6
    tasks = tasks if tasks is not None else [t['id'] for t in dev_tasks()]
    for run, ref in runs.items():
        manifest = PUBLIC / run / 'manifest.json'
        if manifest.exists():
            m = load(manifest)
            assert (m['ref'], m['pool'], m['concurrency'], m['model'], m['protocol']) == (
                ref, pool, concurrency, 'deepseek-flash', 'chat'), run
            if pool == DEV_POOL:
                assert set(m['tasks']) == set(tasks), run
            assert m['key_env'] == 'DEEPSEEKAPI_KEY' and m['base_url_env'] == 'DEEPSEEKBASE_URL'
            assert m['acceptance'] == 'benchmark'
            continue
        assert not (PRIVATE / run).exists(), f'{run}: incomplete preparation needs inspection'
        assert sh(CONTROLLER, EVALUATE, 'prepare', run, '--ref', ref, '--tasks', *tasks,
                  '--pool', pool, '--concurrency', concurrency, *MODEL_ARGS,
                  log=WORK / f'{run}.log') == 0, run


def evaluate(runs, *, concurrency=2, pool=DEV_POOL, tasks=None):
    WORK.mkdir(parents=True, exist_ok=True)
    with (WORK / 'evaluation.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        prepare(runs, concurrency=concurrency, pool=pool, tasks=tasks)
        record = HERE / 'decisions' / ('batch-' + next(iter(runs)) + '.json')
        if record.exists():
            status = load(record)
            assert isinstance(status, list) and {row['run'] for row in status} == set(runs)
            assert all(row.get('complete') for row in status), 'Interrupted batch: do not resume or resample'
            return status
        # Reserve the entire batch before launch: a failed startup must not allow later
        # arms to be paired against old completed results on a second invocation.
        assert not any(list((PRIVATE / run / 'runs').glob('*/launch.json')) for run in runs), \
            'Previously started episodes without a complete batch record'
        record.parent.mkdir(parents=True, exist_ok=True)
        with record.open('x') as out:
            json.dump({'runs': runs, 'started': time.time(), 'complete': False}, out)
        barrier = threading.Barrier(len(runs))

        def one(run):
            barrier.wait(timeout=30)
            started = time.time()
            code = sh(CONTROLLER, EVALUATE, 'run', run, log=WORK / f'{run}.log')
            if code:
                return {'run': run, 'run_exit': code, 'started': started, 'ended': time.time()}
            code = sh(CONTROLLER, EVALUATE, 'results', run, log=WORK / f'{run}.log')
            result = {'run': run, 'run_exit': 0, 'results_exit': code,
                      'started': started, 'ended': time.time()}
            if code == 0:
                summary = load(PUBLIC / run / 'summary.json')
                expected = len(load(PUBLIC / run / 'manifest.json')['tasks'])
                result['complete'] = summary['finished'] == summary['graded'] == expected
            return result

        with concurrent.futures.ThreadPoolExecutor(max_workers=len(runs)) as executor:
            status = list(executor.map(one, runs))
        save(record, status)
        assert all(row.get('complete') for row in status), status
        return status


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('generation', type=int, choices=(1, 2))
    parser.add_argument('--base', required=True)
    parser.add_argument('--base-runs', nargs='+', required=True)
    args = parser.parse_args()
    g = args.generation
    log_path = WORK / 'log.json'
    log = load(log_path) if log_path.exists() else []
    finished = HERE / 'decisions' / f'generation-{g}.json'
    if finished.exists():
        print(json.dumps(load(finished)))
        return
    assert not any(entry['name'].startswith(f'g{g}') for entry in log), 'Partial generation decision'
    noise = load(HERE / 'decisions/seed-null.json')
    assert not noise['stop']
    if g == 2:
        assert load(HERE / 'decisions/generation-1.json')['continue_search']
    a = propose(f'g{g}a', args.base, args.base_runs)
    sibling = WORK / f'g{g}a/workspace/proposal.md'
    b = propose(f'g{g}b', args.base, args.base_runs, sibling if sibling.exists() else None)
    candidates = {r['name']: r for r in (a, b)}
    entries = {}
    valid = []
    for name, report in candidates.items():
        proposal = WORK / name / 'workspace/proposal.md'
        entries[name] = {'name': name, 'base': args.base,
                         'summary': proposal.read_text() if proposal.exists() else '',
                         'proposer_cost': report['proposer_cost'], 'guard_problems': report['problems']}
        if report['accepted_for_evaluation']:
            valid.append(name)
        else:
            entries[name]['decision'] = 'rejected by guard: ' + '; '.join(report['problems'])
    seed_run = f'evo2-g{g}-seed'
    if valid:
        evaluate({seed_run: SEED, **{f'evo2-{name}': f'evolve2-{name}' for name in valid}})
    passed = []
    stop = False
    for name in valid:
        result = analyze.compare(name + '-paired', [f'evo2-{name}'], [seed_run])
        entries[name]['paired'] = result
        stop |= result['stop']
        if result['accepted']:
            passed.append((result['requests']['ratio'], name))
        else:
            entries[name]['decision'] = 'stopped: more than half dropped' if result['stop'] else 'rejected at paired evaluation'
    base, base_runs = args.base, args.base_runs
    for rank, (_, name) in enumerate(sorted(passed)):
        entries[name]['decision'] = 'accepted' if rank == 0 else 'passed but not chosen (higher request ratio)'
        if rank == 0:
            base, base_runs = 'evolve2-' + name, [f'evo2-{name}']
    if stop:
        # A damaged generation cannot select a new base from the other candidate.
        for _, name in passed:
            entries[name]['decision'] = 'stopped: another comparison lost more than half the tasks'
        base, base_runs = args.base, args.base_runs
    continue_search = g == 1 and not stop and any(
        entry.get('paired', {}).get('requests', {}).get('ratio', 1) < 1
        or entry['decision'] == 'accepted' for entry in entries.values())
    log += list(entries.values())
    save(log_path, log)
    save(HERE / 'log.json', log)
    result = {'generation': g, 'next_base': base, 'next_base_runs': base_runs,
              'continue_search': continue_search, 'stop': stop,
              'decisions': {n: e['decision'] for n, e in entries.items()}}
    save(finished, result)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
