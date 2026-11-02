"""Run one generation of the pre-registered loop (PROTOCOL.md "搜索与接受规则").

  generation.py N --base TAG --base-runs RUN... --seeds RUN...

Proposes candidates A and B from the base, evaluates each valid one once on the dev set
(two runs in parallel, 3 episodes each), confirms screened ones with a second run, and records
every decision in artifacts/private/evolve-20261102/log.json and decisions/. Prints the next base.
"""
import argparse
import concurrent.futures
import json
import shutil
import subprocess
import sys

import analyze
from common import CONTROLLER, DEV_POOL, HERE, PRIVATE, PUBLIC, REPO, V12, WORK, load, save

EVALUATE = V12 / 'evaluate.py'


def sh(*args, log=None):
    with open(log, 'a') if log else open('/dev/null', 'w') as out:
        return subprocess.run([str(a) for a in args], cwd=REPO, stdout=out, stderr=subprocess.STDOUT).returncode


def propose(name, base, runs, sibling=None):
    report = WORK / name / 'report.json'
    if report.exists():  # resume: a finished proposal is reused, never re-sampled
        return load(report)
    assert not (WORK / name).exists(), f'{name}: proposal crashed earlier; inspect and delete it before rerunning'
    args = [sys.executable, HERE / 'propose.py', name, '--base', base, '--runs', *runs]
    if sibling:
        args += ['--sibling', sibling]
    sh(*args, log=WORK / f'{name}.log')
    return load(report) if report.exists() else {'name': name, 'problems': ['proposer crashed'], 'accepted_for_evaluation': False}


def resume(run):
    """Make an interrupted run restartable: drop half-finished task dirs (launch.json without
    result.json, which `run` would otherwise skip), the stale gateway socket, relay and network."""
    root = PRIVATE / run
    for job in (root / 'runs').glob('*') if (root / 'runs').exists() else []:
        if (job / 'launch.json').exists() and not (job / 'result.json').exists():
            shutil.rmtree(job)
    (root / 'relay/model.sock').unlink(missing_ok=True)
    sh('docker', 'rm', '-f', 'aster-bench-gateway-' + run)
    sh('docker', 'network', 'rm', 'aster-v12-' + run)


def evaluate(runs):
    """runs: {run_name: git_ref}. Prepare (or resume), run and summarize, all in parallel."""
    for run, ref in runs.items():
        if (PRIVATE / run).exists():
            manifest = load(PUBLIC / run / 'manifest.json')
            assert manifest['ref'] == ref, f'{run} was prepared from {manifest["ref"]}, not {ref}'
            resume(run)
            continue
        assert sh(CONTROLLER, EVALUATE, 'prepare', run, '--ref', ref, '--tasks', DEV_POOL.split('-')[0],
                  '--pool', DEV_POOL, '--concurrency', 3, log=WORK / f'{run}.log') == 0, run

    def one(run):
        sh(CONTROLLER, EVALUATE, 'run', run, log=WORK / f'{run}.log')
        return sh(CONTROLLER, EVALUATE, 'results', run, log=WORK / f'{run}.log')

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        codes = dict(zip(runs, pool.map(one, runs), strict=True))
    assert all(code == 0 for code in codes.values()), codes


def summary(name):
    path = WORK / name / 'workspace/proposal.md'
    return path.read_text()[:3000] if path.exists() else ''


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('generation', type=int)
    parser.add_argument('--base', required=True)
    parser.add_argument('--base-runs', nargs='+', required=True)
    parser.add_argument('--seeds', nargs='+', required=True)
    args = parser.parse_args()
    g = args.generation
    log_path = WORK / 'log.json'
    log = load(log_path) if log_path.exists() else []

    a = propose(f'g{g}a', args.base, args.base_runs)
    sibling = WORK / f'g{g}a/workspace/proposal.md'
    b = propose(f'g{g}b', args.base, args.base_runs, sibling if sibling.exists() else None)
    candidates = {r['name']: r for r in (a, b)}
    entries = {name: {'name': name, 'base': args.base, 'summary': summary(name),
                      'proposer_cost': r.get('proposer_cost'), 'guard_problems': r['problems']}
               for name, r in candidates.items()}
    valid = [name for name, r in candidates.items() if r['accepted_for_evaluation']]
    for name in set(candidates) - set(valid):
        entries[name]['decision'] = 'rejected by guard: ' + '; '.join(candidates[name]['problems'])

    if valid:
        evaluate({f'evo-{name}-r1': f'evolve-{name}' for name in valid})
    screened = []
    for name in valid:
        result = analyze.compare(f'{name}-screen', [f'evo-{name}-r1'], args.seeds)
        entries[name]['screen'] = {k: result[k] for k in ('cost', 'requests', 'passed', 'normal_delivery_and_passed',
                                                          'checks', 'accepted', 'tasks', 'threshold')}
        if result['accepted']:
            screened.append(name)
        else:
            entries[name]['decision'] = 'rejected at screen'

    if screened:
        evaluate({f'evo-{name}-r2': f'evolve-{name}' for name in screened})
    confirmed = []
    for name in screened:
        result = analyze.compare(f'{name}-confirm', [f'evo-{name}-r1', f'evo-{name}-r2'], args.seeds)
        entries[name]['confirm'] = {k: result[k] for k in ('cost', 'requests', 'passed', 'normal_delivery_and_passed',
                                                           'checks', 'accepted', 'tasks', 'threshold')}
        if result['accepted']:
            confirmed.append((result['cost']['ratio'], name))
        else:
            entries[name]['decision'] = 'rejected at confirmation'

    base, base_runs = args.base, args.base_runs
    for rank, (_, name) in enumerate(sorted(confirmed)):
        entries[name]['decision'] = 'accepted' if rank == 0 else 'confirmed but not chosen (higher cost ratio)'
        if rank == 0:
            base, base_runs = f'evolve-{name}', [f'evo-{name}-r1', f'evo-{name}-r2']
    log += list(entries.values())
    save(log_path, log)
    save(HERE / 'log.json', log)
    print(json.dumps({'generation': g, 'next_base': base, 'next_base_runs': base_runs,
                      'decisions': {n: e['decision'] for n, e in entries.items()}}))


if __name__ == '__main__':
    main()
