"""Paired comparisons and the pre-registered acceptance rule (docs/evolve-plan.md §3.4). No model calls.

  analyze.py null SEED_RUN...                      seed-vs-seed noise of one run against the others
  analyze.py compare NAME --cand RUN... --seed RUN...  candidate vs seed, with decision on dev

Tasks where any compared run ended with provider_error, had >=3 request errors or no usage at all
are dropped from every arm and listed. Cost = weighted USD-equivalent with missing usage filled at
the task's own per-request mean (common.run_rows).
"""
import argparse
import random
import statistics

from common import HERE, run_rows, save

BOOTSTRAP = 4000
ACCEPT_QUANTILE = 0.10  # candidate cost ratio must fall below the seed's 10th-percentile chance ratio


def common(runs):
    tables = [run_rows(name) for name in runs]
    shared = set.intersection(*(set(table) for table in tables))
    dropped = sorted(task for task in shared if any(table[task]['unreliable'] for table in tables))
    missing = sorted(set.union(*(set(table) for table in tables)) - shared)
    return tables, sorted(shared - set(dropped)), dropped + missing


def mean_by_task(tables, tasks, key):
    return {task: statistics.mean(float(table[task][key]) for table in tables) for task in tasks}


def ratio_samples(numerator, denominator, tasks, seed):
    rng = random.Random(seed)
    samples = []
    for _ in range(BOOTSTRAP):
        pick = [rng.choice(tasks) for _ in tasks]
        bottom = sum(denominator[t] for t in pick)
        samples.append(sum(numerator[t] for t in pick) / bottom if bottom else 1.0)
    return sorted(samples)


def quantile(samples, q):
    return samples[min(len(samples) - 1, int(q * len(samples)))]


def null(seeds):
    tables, tasks, dropped = common(seeds)
    samples, points = [], []
    for index, table in enumerate(tables):
        others = [other for j, other in enumerate(tables) if j != index]
        one = {t: table[t]['cost'] for t in tasks}
        rest = mean_by_task(others, tasks, 'cost')
        points.append(sum(one.values()) / sum(rest.values()))
        samples += ratio_samples(one, rest, tasks, seed=index)
    samples.sort()
    return {'seeds': seeds, 'tasks': len(tasks), 'dropped': dropped, 'point_ratios': points,
            'quantiles': {str(q): quantile(samples, q) for q in (0.05, 0.10, 0.50, 0.90, 0.95)}}


def compare(name, cands, seeds):
    noise = null(seeds)
    tables, tasks, dropped = common(cands + seeds)
    cand, seed = tables[:len(cands)], tables[len(cands):]
    result = {'name': name, 'candidate_runs': cands, 'seed_runs': seeds, 'tasks': len(tasks), 'dropped': dropped}
    for key in ('cost', 'requests', 'passed', 'normal_delivery_and_passed'):
        c, s = mean_by_task(cand, tasks, key), mean_by_task(seed, tasks, key)
        result[key] = {'candidate': sum(c.values()), 'seed': sum(s.values())}
        if key in ('cost', 'requests'):
            samples = ratio_samples(c, s, tasks, seed=7)
            result[key].update(ratio=sum(c.values()) / sum(s.values()),
                               ci90=[quantile(samples, 0.05), quantile(samples, 0.95)])
    costs = mean_by_task(seed, tasks, 'cost')
    top = max(tasks, key=lambda t: costs[t])
    rest = [t for t in tasks if t != top]
    c, s = mean_by_task(cand, rest, 'cost'), mean_by_task(seed, rest, 'cost')
    result['cost_without_top_seed_task'] = {'task': top, 'ratio': sum(c.values()) / sum(s.values())}
    warn = lambda group: statistics.mean(sum(t[task]['stop_reason'] == 'completed_with_warning' for task in tasks)
                                          for t in group)
    result['completed_with_warning'] = {'candidate': warn(cand), 'seed': warn(seed)}
    threshold = noise['quantiles'][str(ACCEPT_QUANTILE)]
    checks = {
        'cost_below_noise': result['cost']['ratio'] < threshold,
        'passes_not_lower_by_more_than_1': result['passed']['candidate'] >= result['passed']['seed'] - 1,
        'normal_delivery_not_lower_by_more_than_1':
            result['normal_delivery_and_passed']['candidate'] >= result['normal_delivery_and_passed']['seed'] - 1,
        'warnings_not_higher_by_more_than_1':
            result['completed_with_warning']['candidate'] <= result['completed_with_warning']['seed'] + 1,
    }
    result.update(noise=noise, threshold=threshold, checks=checks, accepted=all(checks.values()))
    save(HERE / 'decisions' / (name + '.json'), result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('mode', choices=['null', 'compare'])
    parser.add_argument('runs', nargs='*')
    parser.add_argument('--name')
    parser.add_argument('--cand', nargs='+', default=[])
    parser.add_argument('--seed', nargs='+', default=[])
    args = parser.parse_args()
    import json
    if args.mode == 'null':
        print(json.dumps(null(args.runs), indent=2))
    else:
        print(json.dumps(compare(args.name, args.cand, args.seed), indent=2))


if __name__ == '__main__':
    main()
