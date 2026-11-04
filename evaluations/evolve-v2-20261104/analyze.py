"""Frozen paired request-count analysis. No model calls.

Noise orientation is fixed: evo2-seed-a / evo2-seed-b, 4,000 task bootstrap draws.
Search compares each candidate only with its contemporaneous seed. Final evaluation
averages the two runs per task, retaining the intersection across all four runs.
"""
import argparse
import random
import statistics

from common import HERE, PUBLIC, load, run_rows, save

BOOTSTRAP = 4000
ACCEPT_QUANTILE = 0.10


def common(runs):
    expected = [set(load(PUBLIC / name / 'manifest.json')['tasks']) for name in runs]
    assert all(tasks == expected[0] for tasks in expected), 'Compared manifests differ'
    tables = [run_rows(name) for name in runs]
    assert all(set(table) <= expected[0] for table in tables), 'Unexpected task result'
    shared = set.intersection(*(set(table) for table in tables))
    tasks = sorted(t for t in shared if not any(table[t]['unreliable'] for table in tables))
    dropped = sorted(expected[0] - set(tasks))
    return tables, tasks, dropped


def mean_by_task(tables, tasks, key):
    return {task: statistics.mean(float(table[task][key]) for table in tables) for task in tasks}


def samples(numerator, denominator, tasks, *, difference=False):
    assert tasks, 'No comparable tasks'
    rng = random.Random(7)
    result = []
    for _ in range(BOOTSTRAP):
        pick = [rng.choice(tasks) for _ in tasks]
        top, bottom = sum(numerator[t] for t in pick), sum(denominator[t] for t in pick)
        result.append(top - bottom if difference else top / bottom)
    return sorted(result)


def quantile(values, q):
    return values[min(len(values) - 1, int(q * len(values)))]


def drop_reasons(runs, tables, dropped):
    result = {}
    for task in dropped:
        causes = {}
        for name, table in zip(runs, tables, strict=True):
            row = table.get(task)
            if row is None:
                causes[name] = ['missing task result']
                continue
            why = []
            if row['stop_reason'] == 'provider_error':
                why.append('provider_error')
            if row['request_errors'] >= 3:
                why.append(f"request_errors={row['request_errors']}")
            if row['requests'] - row['missing_usage'] <= 0:
                why.append('no usage records')
            if why:
                causes[name] = why
        result[task] = causes
    return result


def null(seeds):
    assert seeds == ['evo2-seed-a', 'evo2-seed-b'], 'Noise orientation is fixed'
    tables, tasks, dropped = common(seeds)
    result = {'seeds': seeds, 'orientation': 'seed_a / seed_b', 'metric': 'requests',
              'tasks': len(tasks), 'dropped': dropped,
              'drop_reasons': drop_reasons(seeds, tables, dropped), 'bootstrap': BOOTSTRAP}
    if not tasks or len(dropped) > len(tasks):
        return {**result, 'stop': True, 'reason': 'more_than_half_dropped', 'threshold': None}
    a = mean_by_task(tables[:1], tasks, 'requests')
    b = mean_by_task(tables[1:], tasks, 'requests')
    draws = samples(a, b, tasks)
    threshold = quantile(draws, ACCEPT_QUANTILE)
    return {**result, 'point_ratio': sum(a.values()) / sum(b.values()),
            'quantiles': {str(q): quantile(draws, q) for q in (.05, .1, .5, .9, .95)},
            'threshold': threshold, 'stop': threshold < .75,
            'reason': 'noise_threshold_below_0.75' if threshold < .75 else None}


def compare(name, cands, seeds, *, heldout=False):
    assert len(cands) == len(seeds) == (2 if heldout else 1)
    runs = cands + seeds
    tables, tasks, dropped = common(runs)
    cand, seed = tables[:len(cands)], tables[len(cands):]
    result = {'name': name, 'candidate_runs': cands, 'seed_runs': seeds,
              'tasks': len(tasks), 'task_ids': tasks, 'dropped': dropped,
              'drop_reasons': drop_reasons(runs, tables, dropped), 'bootstrap': BOOTSTRAP,
              'stop': not tasks or len(dropped) > len(tasks)}
    for key in ('requests', 'cost', 'passed', 'normal_delivery_and_passed'):
        c, s = mean_by_task(cand, tasks, key), mean_by_task(seed, tasks, key)
        metric = {'candidate': sum(c.values()), 'seed': sum(s.values())}
        if tasks:
            difference = key in ('passed', 'normal_delivery_and_passed')
            if key == 'cost' and min(s.values()) <= 0:
                metric.update(ratio=sum(c.values()) / sum(s.values()) if sum(s.values()) else None,
                              ci90=None, unavailable_reason='zero-cost seed task; ratio bootstrap undefined')
                result[key] = metric
                continue
            draws = samples(c, s, tasks, difference=difference)
            metric.update(ci90=[quantile(draws, .05), quantile(draws, .95)])
            metric['difference' if difference else 'ratio'] = (
                sum(c.values()) - sum(s.values()) if difference else sum(c.values()) / sum(s.values()))
        result[key] = metric
    result['completed_with_warning'] = {
        arm: statistics.mean(sum(table[t]['stop_reason'] == 'completed_with_warning' for t in tasks)
                             for table in group)
        for arm, group in (('candidate', cand), ('seed', seed))}
    if heldout:
        repository = [t for t in tasks if seed[0][t]['kind'] == 'repository']
        c, s = mean_by_task(cand, repository, 'requests'), mean_by_task(seed, repository, 'requests')
        result['repository_requests'] = {'tasks': len(repository), 'task_ids': repository}
        if repository:
            draws = samples(c, s, repository)
            result['repository_requests'].update(
                candidate=sum(c.values()), seed=sum(s.values()), ratio=sum(c.values()) / sum(s.values()),
                ci90=[quantile(draws, .05), quantile(draws, .95)])
    else:
        noise = load(HERE / 'decisions/seed-null.json')
        assert not noise['stop'], 'Noise stage requires stopping'
        threshold = noise['threshold']
        checks = {
            'requests_below_noise': bool(tasks) and result['requests']['ratio'] < threshold,
            'passes_not_lower_by_more_than_1': result['passed']['candidate'] >= result['passed']['seed'] - 1,
            'normal_delivery_not_lower_by_more_than_1':
                result['normal_delivery_and_passed']['candidate'] >= result['normal_delivery_and_passed']['seed'] - 1,
            'warnings_not_higher_by_more_than_1':
                result['completed_with_warning']['candidate'] <= result['completed_with_warning']['seed'] + 1,
        }
        result.update(threshold=threshold, checks=checks, accepted=not result['stop'] and all(checks.values()))
    save(HERE / 'decisions' / (name + '.json'), result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['null', 'compare'])
    parser.add_argument('runs', nargs='*')
    parser.add_argument('--name')
    parser.add_argument('--cand', nargs='+', default=[])
    parser.add_argument('--seed', nargs='+', default=[])
    parser.add_argument('--heldout', action='store_true')
    args = parser.parse_args()
    import json
    if args.mode == 'null':
        result = null(args.runs)
        save(HERE / 'decisions/seed-null.json', result)
    else:
        result = compare(args.name, args.cand, args.seed, heldout=args.heldout)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
