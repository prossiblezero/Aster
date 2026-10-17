"""Paired comparison on fresh30 with bootstrap confidence intervals. Offline only.

Usage: analyze.py label=run[,run...] ...   (several runs per label are repeats; per-task
values are averaged across repeats before resampling tasks)
"""
import json
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUNS = HERE.parent / 'harness-v12-20261017' / 'runs'
METRICS = ('passed', 'normal_delivery_and_passed', 'requests', 'weighted_cost')


def per_task(runs):
    table = {}
    for run in runs:
        for row in json.loads((RUNS / run / 'results.json').read_text()):
            table.setdefault(row['task'], []).append(row)
    return {task: {m: sum(float(r[m]) for r in rows) / len(rows) for m in METRICS} for task, rows in table.items()}


def bootstrap(values, rounds=10000, seed=20261018):
    rng = random.Random(seed)
    n = len(values)
    means = sorted(sum(values[rng.randrange(n)] for _ in range(n)) / n for _ in range(rounds))
    return means[int(0.025 * rounds)], means[int(0.975 * rounds)]


def main():
    labels = dict(arg.split('=', 1) for arg in sys.argv[1:])
    data = {label: per_task(runs.split(',')) for label, runs in labels.items()}
    tasks = sorted(set.intersection(*(set(d) for d in data.values())))
    report = {'tasks': len(tasks), 'agents': {}, 'paired': {}}
    for label, table in data.items():
        report['agents'][label] = {}
        for m in METRICS:
            values = [table[t][m] for t in tasks]
            total = sum(values)
            low, high = bootstrap(values)
            report['agents'][label][m] = {'total': round(total, 3), 'ci95_total': [round(low * len(tasks), 3), round(high * len(tasks), 3)]}
    names = list(data)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            report['paired'][f'{a} - {b}'] = {}
            for m in METRICS:
                diffs = [data[a][t][m] - data[b][t][m] for t in tasks]
                low, high = bootstrap(diffs)
                report['paired'][f'{a} - {b}'][m] = {'difference': round(sum(diffs), 3),
                                                     'ci95': [round(low * len(tasks), 3), round(high * len(tasks), 3)]}
    print(json.dumps(report, indent=2))
    return report


if __name__ == '__main__':
    main()
