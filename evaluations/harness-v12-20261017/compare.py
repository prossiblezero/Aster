"""Compare a v12 run with v11 (round 1 attempt 1) and pi/Codex on the same tasks. Offline only."""
import argparse
import glob
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
V11 = REPO / 'evaluations/turn-reduction-20261015/round1/attempt1'
V11_RUNS = REPO / 'artifacts/private/turn-reduction-20261015/round1/attempt1/runs'
G50 = REPO / 'evaluations/general-coding-50-20261012/results.json'
G50_RUNS = REPO / 'artifacts/private/general-coding-50-20261012/runs'


def weighted(r):
    c = r['cached_tokens']
    return ((r['input_tokens'] - c) * 1.25 + c * 0.125 + r['output_tokens'] * 10) / 1e6


def model_seconds(out):
    total = 0.0
    for path in glob.glob(str(out / 'provider' / '*.json')):
        if not path.endswith('.request.json'):
            total += json.load(open(path)).get('seconds', 0)
    return total


def summarize(rows, runs, prefix):
    s = dict(n=len(rows), passed=sum(r['passed'] for r in rows),
             normal=sum(r.get('normal_delivery_and_passed', False) for r in rows),
             requests=sum(r['requests'] for r in rows), weighted=round(sum(weighted(r) for r in rows), 3),
             input=sum(r['input_tokens'] for r in rows), uncached=sum(r['input_tokens'] - r['cached_tokens'] for r in rows),
             seconds=round(sum(r['seconds'] for r in rows)))
    ms = sum(model_seconds(runs / f"{prefix}-{r['task']}") for r in rows)
    s['model_seconds'], s['other_seconds'] = round(ms), round(s['seconds'] - ms)
    return s


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('name')
    args = parser.parse_args()
    run_dir = HERE / 'runs' / args.name
    rows = json.load(open(run_dir / 'results.json'))
    tasks = {r['task'] for r in rows}
    table = {'v12 ' + args.name: summarize(rows, REPO / 'artifacts/private' / HERE.name / args.name / 'runs', 'aster')}
    if (V11 / 'results.json').exists():
        v11 = [r for r in json.load(open(V11 / 'results.json')) if r['task'] in tasks]
        if len(v11) == len(rows):
            table['v11 stage1+2'] = summarize(v11, V11_RUNS, 'aster')
    g50 = json.load(open(G50))
    for agent in ('aster', 'pi', 'codex'):
        sel = [r for r in g50 if r['agent'] == agent and r['task'] in tasks]
        table[{'aster': 'c6b651d (pre-opt)'}.get(agent, agent)] = summarize(sel, G50_RUNS, agent)
    keys = ['n', 'passed', 'normal', 'requests', 'weighted', 'input', 'uncached', 'seconds', 'model_seconds', 'other_seconds']
    print('| version | ' + ' | '.join(keys) + ' |')
    print('|---|' + '---:|' * len(keys))
    for name, s in table.items():
        print(f'| {name} | ' + ' | '.join(str(s[k]) for k in keys) + ' |')
    per = []
    v11rows = {r['task']: r for r in json.load(open(V11 / 'results.json'))} if (V11 / 'results.json').exists() else {}
    pi = {r['task']: r for r in g50 if r['agent'] == 'pi'}
    for r in sorted(rows, key=lambda r: r['task']):
        o = v11rows.get(r['task'])
        per.append((r['task'], r['passed'], o['passed'] if o else None, pi[r['task']]['passed'],
                    r['requests'], o['requests'] if o else None, pi[r['task']]['requests'], r.get('stop_reason')))
    print('\n| task | v12 pass | v11 pass | pi pass | v12 req | v11 req | pi req | v12 stop |\n|---|---|---|---|---:|---:|---:|---|')
    for p in per:
        print('| ' + ' | '.join(str(x) for x in p) + ' |')
    (run_dir / 'comparison.json').write_text(json.dumps(table, indent=2) + '\n')


if __name__ == '__main__':
    sys.exit(main())
