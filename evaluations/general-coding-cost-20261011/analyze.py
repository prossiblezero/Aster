"""Postprocess frozen runs; no model calls and no historical result replacement."""
import json
from collections import Counter

import evaluate as runner

ROOT, WORK = runner.ROOT, runner.WORK
common = runner.imported('cost_usage', runner.ORIGINAL / 'summarize.py')


def trace(phase):
    total, memory, changes = Counter(), Counter(), Counter()
    per_task = []
    for task in runner.load(WORK / phase / 'tasks.json'):
        root = WORK / phase / 'runs' / ('aster-' + task['id'])
        counts, requests = common.usage(root)
        total.update(counts)
        previous = None
        task_changes = Counter()
        for meta in sorted(requests, key=lambda r: r.get('started') or (
            root / 'provider' / (r['request_id'] + '.request.json')).stat().st_mtime):
            request = runner.load(root / 'provider' / (meta['request_id'] + '.request.json'))
            items = request.get('input', [])
            foreground = bool(items and 'You are Aster, a local coding agent.' in str(items[0].get('content', '')))
            if not foreground:
                memory['requests'] += 1
                usage = meta.get('usage') or {}
                memory.update({k: usage.get(k, 0) for k in ('input_tokens', 'output_tokens', 'total_tokens')})
                memory['missing_usage'] += not bool(usage)
                continue
            task_changes['foreground_requests'] += 1
            if previous is not None:
                prior = previous['input']
                stable = prior[:-1] if str(prior[-1].get('content', '')).startswith('Context sections') else prior
                changed = items[:len(stable)] != stable
                task_changes['comparisons'] += 1
                task_changes['history_prefix_changes'] += changed
                task_changes['tool_definition_changes'] += previous['tools'] != request['tools']
            previous = request
        changes.update(task_changes)
        per_task.append({'task': task['id'], **counts, **task_changes})
    return {'usage': dict(total), 'memory': dict(memory), 'prefix': dict(changes), 'tasks': per_task}


def cost(summary, r, q):
    return (summary['input_tokens'] - summary['cached_tokens']
            + r * summary['cached_tokens'] + q * summary['output_tokens'])


def paired(before, after):
    before = {row['task']: row for row in before}
    after = {row['task']: row for row in after}
    tasks = sorted(before.keys() & after.keys())
    passed = [task for task in tasks if before[task]['passed'] and after[task]['passed']]
    metrics = ('total_tokens', 'input_tokens', 'cached_tokens', 'output_tokens',
               'requests', 'missing_usage', 'seconds')
    totals = {}
    for name, rows in (('before', before), ('after', after)):
        totals[name] = {key: sum(rows[task][key] for task in passed) for key in metrics}
        totals[name]['uncached_input'] = totals[name]['input_tokens'] - totals[name]['cached_tokens']
    return {
        'common_passed_tasks': passed, 'common_passed_totals': totals,
        'gained': [task for task in tasks if not before[task]['passed'] and after[task]['passed']],
        'lost': [task for task in tasks if before[task]['passed'] and not after[task]['passed']],
    }


def main():
    results = {}
    for phase in runner.PHASES:
        path = ROOT / phase / 'summary.json'
        if path.exists():
            results[phase] = {'summary': runner.load(path), 'trace': trace(phase)}
    if all(p in results for p in ('pilot-baseline', 'pilot-candidate')):
        before, after = [results[p]['summary'] for p in ('pilot-baseline', 'pilot-candidate')]
        weights = [{'cache_weight': r, 'output_weight': q, 'baseline': cost(before, r, q),
                    'candidate': cost(after, r, q), 'change_percent': 100 * (cost(after, r, q) / cost(before, r, q) - 1)}
                   for r in (0.1, 0.25) for q in (1, 4, 8)]
        results['pilot_gate'] = {
            'all_graded': before['graded'] == after['graded'] == 3,
            'no_grading_errors': before['grading_errors'] == after['grading_errors'] == 0,
            'quality': after['passed'] >= before['passed'],
            'uncached_input': after['input_tokens'] - after['cached_tokens'] < before['input_tokens'] - before['cached_tokens'],
            'all_cost_weights': all(r['candidate'] <= r['baseline'] for r in weights),
            'weights': weights,
            'missing_usage': {'baseline': before['missing_usage'], 'candidate': after['missing_usage']},
        }
        results['pilot_gate']['proceed'] = all(results['pilot_gate'][k] for k in (
            'all_graded', 'no_grading_errors', 'quality', 'uncached_input', 'all_cost_weights'))
        results['pilot_paired'] = paired(*[
            runner.load(ROOT / phase / 'results.json')
            for phase in ('pilot-baseline', 'pilot-candidate')])
    full = results.get('full-candidate', {}).get('summary', {})
    if full.get('finished') == full.get('graded') == 20:
        old_root = ROOT.parent / 'general-coding-optimized-v2-20261011'
        original = runner.load(runner.ORIGINAL / 'summary.json')['agents']
        versions = {'aster-v2': runner.load(old_root / 'summary.json'),
                    'aster-candidate': full, 'pi-frozen': original['pi'],
                    'codex-frozen': original['codex']}
        results['full_comparison'] = {
            'versions': versions,
            'paired_with_v2': paired(runner.load(old_root / 'results.json'),
                                     runner.load(ROOT / 'full-candidate/results.json')),
            'weights': [
                {'cache_weight': r, 'output_weight': q,
                 'weighted_usage': {name: cost(value, r, q) for name, value in versions.items()}}
                for r in (0.1, 0.25) for q in (1, 4, 8)],
        }
        metrics = ('finished', 'seconds', 'requests', 'missing_usage', 'input_tokens',
                   'cached_tokens', 'output_tokens', 'total_tokens')
        results['development_total'] = {
            key: sum(results[phase]['summary'][key] for phase in runner.PHASES)
            for key in metrics}
        for phase in runner.PHASES:
            for key, value in results[phase]['trace']['usage'].items():
                assert results[phase]['summary'][key] == value, (phase, key)
    runner.save(ROOT / 'comparison.json', results)
    print(json.dumps({k: v['summary'] if 'summary' in v else v for k, v in results.items()}, indent=2))


if __name__ == '__main__':
    main()
