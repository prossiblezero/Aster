"""Publish sanitized paired metrics without model traces or hidden test material."""
from __future__ import annotations

import hashlib
import json
import statistics
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
PRIVATE = REPO / 'artifacts/private/worker-code-20260928'


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def stats(rows):
    usage = Counter()
    for r in rows:
        usage.update(r['usage'])
    return {'episodes': len(rows), 'passed': sum(r['passed'] for r in rows),
        'runtime_completed': sum(r['phase'] == 'completed' for r in rows),
        'passed_without_runtime_completion': sum(r['passed'] and r['phase'] != 'completed' for r in rows),
        'runtime_completed_but_grader_failed': sum(not r['passed'] and r['phase'] == 'completed' for r in rows),
        'seconds_sum': sum(r['seconds'] for r in rows),
        'seconds_median': statistics.median(r['seconds'] for r in rows) if rows else None,
        'input_tokens': usage.get('input_tokens'), 'output_tokens': usage.get('output_tokens'),
        'cache_read_input_tokens': usage.get('cache_read_input_tokens'),
        'total_tokens': usage.get('total_tokens'),
        'request_usage_coverage': {'observed': sum(r['http_with_total_usage'] for r in rows),
            'attempts': sum(r['http_attempts'] for r in rows)},
        'accounting_matches': all(r['usage_matches'] for r in rows),
        'http_accounting_matches': all(r['http_usage_matches'] for r in rows),
        'child_tokens': sum(r['child_tokens'] for r in rows),
        'worker_modes': dict(sum((Counter(r['worker_modes']) for r in rows), Counter())),
        'worker_stop_reasons': dict(sum((Counter(r['worker_stop_reasons']) for r in rows), Counter())),
        'provider_error_categories': dict(sum((Counter(r['provider_error_categories']) for r in rows), Counter())),
        'child_file_paths': sum(r['child_file_paths'] for r in rows),
        'shared_parent_child_file_paths': sum(r['shared_parent_child_file_paths'] for r in rows),
        **{k: sum(r[k] for r in rows) for k in ['model_calls', 'tool_calls', 'workers_started', 'workers_completed', 'workers_with_edits', 'workers_without_answer_or_edits', 'coordination_calls', 'scope_conflicts']}}


def main():
    tasks = [json.loads(line) for line in (REPO / 'datasets/worker-code-v2/instances.jsonl').read_text().splitlines()]
    rows = [json.loads(p.read_text()) for p in sorted((PRIVATE / 'runs').glob('*/result.json'))]
    checks = {r['id']: r for p in (PRIVATE / 'preflight').glob('*/preflight.json') if (r := json.loads(p.read_text()))}
    for row in rows:
        ledger = json.loads((PRIVATE / 'runs' / (row['task'] + '--' + row['arm']) / 'request-ledger.json').read_text())
        reported = sum((Counter(a.get('usage', {})) for request in ledger for a in request['attempts']), Counter())
        row['http_usage_matches'] = reported == Counter(row['usage'])
        row['child_tokens'] = sum(child['usage'].get('total_tokens', 0)
                                 for child in row['runs'] if child.get('parent_run_id'))
        row['workers_with_edits'] = sum(bool(child.get('changed_paths'))
                                       for child in row['runs'] if child.get('parent_run_id'))
        child_states = [json.loads((Path(row['workspace']) / '.aster/runs' / child['run_id'] / 'checkpoint.json').read_text())['state']
                        for child in row['runs'] if child.get('parent_run_id')]
        row['workers_without_answer_or_edits'] = sum(not s.get('final') and not s.get('changed_paths') for s in child_states)
        row['grade_mode'] = row['grade'].get('mode', checks.get(row['task'], {}).get('mode', 'unspecified'))
        row['worker_modes'] = dict(Counter(t['arguments'].get('mode', 'explore') for t in row['tools']
            if t['name'] == 'worker_start' and t['status'] == 'success'))
        row['worker_stop_reasons'] = dict(Counter(child['stop_reason'] or child['phase']
            for child in row['runs'] if child.get('parent_run_id')))
        categories = Counter()
        for trace in (Path(row['workspace']) / '.aster/runs').glob('*/trace.jsonl'):
            for line in trace.read_text().splitlines():
                if 'provider_error' in line:
                    event = json.loads(line)
                    if event.get('event') == 'provider_error':
                        categories.update([event.get('category', 'unspecified')])
        row['provider_error_categories'] = dict(categories)
        reads = [{t['arguments']['path'] for t in row['tools']
                  if t['name'] == 'read_file' and t['status'] == 'success' and t['child'] == child}
                 for child in [False, True]]
        # Unique (episode, path) observations, not duplicated lines or tokens.
        row['child_file_paths'] = len(reads[1])
        row['shared_parent_child_file_paths'] = len(reads[0] & reads[1])
    pairs = []
    for t in tasks:
        r = {row['arm']: row for row in rows if row['task'] == t['id']}
        if len(r) == 2:
            solo, worker = r['solo'], r['current_worker']
            launches = [json.loads((PRIVATE / 'runs' / (t['id'] + '--' + arm) / 'launch.json').read_text())
                        for arm in ['solo', 'current_worker']]
            for key in ['prompt', 'endpoint_sha256', 'limits', 'source_hashes', 'preparation', 'python', 'aster_import']:
                if launches[0][key] != launches[1][key]:
                    raise ValueError(f'Paired input mismatch: {t["id"]} {key}')
            settings = [{k: v for k, v in launch['settings'].items() if k != 'max_workers'} for launch in launches]
            if settings[0] != settings[1] or solo['product_before'] != worker['product_before']:
                raise ValueError(f'Paired environment/workspace mismatch: {t["id"]}')
            pairs.append({'task': t['id'], 'source': t['source'], 'difficulty': t['difficulty'],
                'cohort': t['collaboration']['role'], 'solo_passed': solo['passed'], 'worker_passed': worker['passed'],
                'worker_used': worker['workers_started'] > 0,
                'solo_tokens': solo['usage'].get('total_tokens'), 'worker_tokens': worker['usage'].get('total_tokens'),
                'solo_seconds': solo['seconds'], 'worker_seconds': worker['seconds'],
                'grade_mode': solo['grade_mode'], 'input_identity_verified': True,
                'driver_files_with_different_hashes': [key for key in sorted(launches[0]['driver_hashes'].keys() | launches[1]['driver_hashes'].keys())
                    if launches[0]['driver_hashes'].get(key) != launches[1]['driver_hashes'].get(key)]})
    pair_ids = {p['task'] for p in pairs}
    paired_rows = [r for r in rows if r['task'] in pair_ids]
    incomplete = []
    for directory in sorted((PRIVATE / 'runs').iterdir()):
        if (directory / 'result.json').exists():
            continue
        ledger = directory / 'request-ledger.json'
        requests = json.loads(ledger.read_text()) if ledger.exists() else []
        reported = Counter()
        for request in requests:
            reported.update(request.get('usage', {}))
        incomplete.append({'episode': directory.name, 'recorded_model_calls': len(requests),
                           'reported_usage': dict(reported)})
    unavailable = [{'task': t['id'], 'source': t['source'], 'preflight': checks.get(t['id'], {'ready': False, 'status': 'not_preflighted'})}
                   for t in tasks if not checks.get(t['id'], {}).get('ready')]
    summary = {'dataset_tasks': len(tasks), 'complete_pairs': len(pairs), 'all_finished_episodes': len(rows),
        'preflight_ready': sum(bool(r.get('ready')) for r in checks.values()),
        'paired_wins': sum(not p['solo_passed'] and p['worker_passed'] for p in pairs),
        'paired_losses': sum(p['solo_passed'] and not p['worker_passed'] for p in pairs),
        'both_passed': sum(p['solo_passed'] and p['worker_passed'] for p in pairs),
        'both_failed': sum(not p['solo_passed'] and not p['worker_passed'] for p in pairs),
        'pairs_using_workers': sum(p['worker_used'] for p in pairs),
        'arms': {arm: stats([r for r in paired_rows if r['arm'] == arm]) for arm in ['solo', 'current_worker']},
        'all_finished_arms': {arm: stats([r for r in rows if r['arm'] == arm]) for arm in ['solo', 'current_worker']},
        'incomplete_episodes': incomplete,
        'grouped': {field: {value: {arm: stats([r for r in paired_rows if r[field] == value and r['arm'] == arm])
                                  for arm in ['solo', 'current_worker']}
                           for value in sorted({r[field] for r in paired_rows})}
                    for field in ['source', 'difficulty', 'cohort', 'grade_mode']}}
    public_rows = [{k: r.get(k) for k in ['task', 'source', 'difficulty', 'cohort', 'arm', 'passed', 'phase', 'stop_reason',
        'seconds', 'grading_seconds', 'usage', 'usage_matches', 'model_calls', 'http_attempts', 'http_with_total_usage',
        'tool_calls', 'workers_started', 'workers_completed', 'coordination_calls', 'scope_conflicts', 'response_models', 'source_unchanged', 'error',
        'http_usage_matches', 'child_tokens', 'workers_with_edits', 'workers_without_answer_or_edits']}
                  | {'grade_mode': r['grade_mode'], 'grade_status': r['grade'].get('status'),
                     'worker_modes': r['worker_modes'], 'child_file_paths': r['child_file_paths'],
                     'worker_stop_reasons': r['worker_stop_reasons'],
                     'provider_error_categories': r['provider_error_categories'],
                     'shared_parent_child_file_paths': r['shared_parent_child_file_paths'],
                     'grade_counts': {k: r['grade'][k] for k in ['expected', 'expected_tests', 'tests',
                         'passed_tests', 'failed_tests', 'errors', 'skipped_tests', 'required_count',
                         'observed_count', 'exit_code', 'returncode', 'timed_out'] if k in r['grade']},
                     'missing_tests': len(r['grade'].get('missing_tests', [])),
                     'repository_test_counts': {key: dict(Counter(r['grade'][key].values()))
                         for key in ['fail_to_pass', 'pass_to_pass'] if key in r['grade']},
                     'non_scoring_failure_labels': len(r['grade'].get('non_scoring_failures', {}))}
                  for r in rows]
    save(HERE / 'results.json', public_rows)
    save(HERE / 'pairs.json', pairs)
    save(HERE / 'summary.json', summary)
    # Detailed test output stays private; publish only reason/status for unavailable tasks.
    save(HERE / 'coverage.json', [{'task': t['id'], 'source': t['source'],
        'ready': bool(checks.get(t['id'], {}).get('ready')),
        'status': checks.get(t['id'], {}).get('status', 'ready' if checks.get(t['id'], {}).get('ready') else 'not_preflighted'),
        'reason': checks.get(t['id'], {}).get('reason', checks.get(t['id'], {}).get('detail', '')),
        'complete_pair': t['id'] in pair_ids} for t in tasks])
    preflights = []
    for task, check in sorted(checks.items()):
        starter, reference = check['starter'], check['reference']
        def passed_tests(grade):
            return grade.get('passed_tests', sum(value == 'PASSED'
                for key in ['fail_to_pass', 'pass_to_pass'] for value in grade.get(key, {}).values()))
        preflights.append({'task': task, 'ready': check['ready'],
            'starter_passed': starter['passed'], 'reference_passed': reference['passed'],
            'expected': next((reference[key] for key in ['expected', 'expected_tests', 'required_count'] if key in reference), None),
            'starter_passed_tests': passed_tests(starter), 'reference_passed_tests': passed_tests(reference),
            'reference_exit': reference.get('exit_code', reference.get('returncode')),
            'mode': reference.get('mode', check.get('mode')),
            'preflight_file_sha256': hashlib.sha256((PRIVATE / 'preflight' / task / 'preflight.json').read_bytes()).hexdigest()})
    save(HERE / 'preflight-summary.json', preflights)
    print(json.dumps({'pairs': len(pairs), 'unavailable':len(unavailable), **summary['arms']}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
