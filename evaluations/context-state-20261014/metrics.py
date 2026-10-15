"""Offline prefix/cache accounting from gateway archives; never calls a model."""

import argparse
import json
import statistics
from pathlib import Path


def canon(item):
    return json.dumps(item, sort_keys=True, ensure_ascii=False)


def percentile(values, fraction):
    # Match the plan's baseline: Python's exclusive, interpolated quantiles.
    if len(values) < 2:
        return values[0] if values else None
    return statistics.quantiles(values, n=100, method='exclusive')[round(fraction * 100) - 1]


def requests(directory):
    records = []
    for path in directory.glob('*.json'):
        if path.name.endswith('.request.json'):
            continue
        payload_path = path.with_name(path.stem + '.request.json')
        if not payload_path.exists():
            raise ValueError(f'Missing request body: {payload_path}')
        metadata = json.loads(path.read_text())
        payload = json.loads(payload_path.read_text())
        if [t.get('name') for t in payload.get('tools', [])] != ['memory_changes']:
            records.append((metadata, payload))
    return sorted(records, key=lambda pair: pair[0]['started'])


def stable(previous, current, *, strict=False):
    left, right = [canon(x) for x in previous], [canon(x) for x in current]
    lcp = 0
    for a, b in zip(left, right, strict=False):
        if a != b:
            break
        lcp += 1
    return lcp >= len(left) - (0 if strict else 1)


def measure(runs, selected=None):
    groups = {}
    for directory in sorted(runs.glob('*/provider')):
        agent, task = directory.parent.name.split('-', 1)
        if selected is not None and task not in selected:
            continue
        group = groups.setdefault(agent, [])
        row = dict(task=task, requests=0, input_items=0, followups=0, rewrites=0, strict_rewrites=0, low_hits=0, rewrite_uncached=0,
                   stable_miss=0, input=0, cached=0, missing_usage=0, cache_keys=0, hits=[])
        previous = None
        for metadata, payload in requests(directory):
            row['requests'] += 1
            row['input_items'] += len(payload['input'])
            row['cache_keys'] += bool(payload.get('prompt_cache_key'))
            usage = metadata.get('usage') or {}
            inputs = usage.get('input_tokens', 0)
            if not inputs:
                row['missing_usage'] += 1
                continue
            cached = usage.get('input_tokens_details', {}).get('cached_tokens', 0)
            row['input'] += inputs
            row['cached'] += cached
            if previous is not None:
                prev_payload, prev_tokens = previous
                row['followups'] += 1
                row['strict_rewrites'] += not stable(prev_payload['input'], payload['input'], strict=True)
                if stable(prev_payload['input'], payload['input']):
                    row['low_hits'] += cached < .85 * prev_tokens
                    row['stable_miss'] += max(0, min(prev_tokens, inputs) - cached)
                    row['hits'].append(min(cached / prev_tokens, 1))
                else:
                    row['rewrites'] += 1
                    row['rewrite_uncached'] += inputs - cached
            previous = payload, inputs
        group.append(row)
    summary = {}
    for agent, rows in groups.items():
        hits = [v for row in rows for v in row.pop('hits')]
        result = {key: sum(row[key] for row in rows) for key in rows[0] if key != 'task'}
        result.update(tasks=len(rows), uncached=result['input'] - result['cached'],
                      mean_input_items=result['input_items'] / result['requests'] if result['requests'] else None,
                      mean_input_tokens=(result['input'] / (result['requests'] - result['missing_usage'])
                                         if result['requests'] > result['missing_usage'] else None),
                      cache_ratio=result['cached'] / result['input'] if result['input'] else None,
                      stable_hit_mean=statistics.mean(hits) if hits else None,
                      stable_hit_p10=percentile(hits, 0.1))
        summary[agent] = result
    return {'agents': summary, 'tasks': groups}


def table(report):
    agents = [a for a in ('aster', 'pi', 'codex') if a in report['agents']]
    lines = ['| Metric | ' + ' | '.join(agents) + ' |',
             '|---|' + '---:|' * len(agents)]
    for key in ('tasks', 'requests', 'followups', 'rewrites', 'strict_rewrites', 'low_hits', 'rewrite_uncached', 'stable_miss',
                'stable_hit_mean', 'stable_hit_p10', 'input', 'cached', 'uncached', 'cache_ratio',
                'missing_usage', 'cache_keys', 'mean_input_items', 'mean_input_tokens'):
        values = [report['agents'][a][key] for a in agents]
        lines.append('| ' + key + ' | ' + ' | '.join(
            f'{v:.3f}' if isinstance(v, float) else str(v) for v in values) + ' |')
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('runs', type=Path)
    parser.add_argument('--results', type=Path)
    parser.add_argument('--exposure', choices=['old20', 'new30'])
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.exposure and not args.results:
        parser.error('--exposure requires --results')
    selected = None
    if args.exposure:
        selected = {r['task'] for r in json.loads(args.results.read_text())
                    if r['exposure'] == args.exposure}
    report = measure(args.runs, selected)
    report['scope'] = args.exposure or 'all'
    if args.output:
        args.output.write_text(json.dumps(report, indent=2) + '\n')
        args.output.with_suffix('.md').write_text(table(report))
    print(table(report), end='')


if __name__ == '__main__':
    main()
