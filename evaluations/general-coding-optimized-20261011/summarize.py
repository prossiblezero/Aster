"""Export and summarize this fixed Aster run without additional model calls."""
import argparse
import gzip
import hashlib
import json
import statistics
import time

import evaluate as runner

HERE, PRIVATE = runner.HERE, runner.PRIVATE
load, save = runner.load, runner.save
common = runner.imported('optimized_summary_common', runner.ORIGINAL / 'summarize.py')
RUN_ID = 'general-optimized-20261011'


def status():
    finished, active = [], []
    for task in load(PRIVATE / 'tasks.json'):
        root = PRIVATE / 'runs' / ('aster-' + task['id'])
        if (root / 'result.json').exists():
            result = load(root / 'result.json')
            state = load(root / 'aster-result.json') if (root / 'aster-result.json').exists() else {}
            finished.append({'task': task['id'], 'seconds': result['seconds'],
                             'phase': state.get('phase'), 'stop': state.get('stop_reason'),
                             'passed': result.get('grade', {}).get('passed'),
                             'patch_bytes': result.get('patch_bytes')})
        elif (root / 'launch.json').exists():
            launch = load(root / 'launch.json')
            active.append({'task': task['id'], 'seconds': round(time.time() - launch['started'])})
    print(json.dumps({'finished': finished, 'active': active}, ensure_ascii=False, indent=2))


def export():
    rows = []
    for task in load(PRIVATE / 'tasks.json'):
        root = PRIVATE / 'runs' / ('aster-' + task['id'])
        if task['kind'] == 'repository' and (root / 'result.json').exists():
            rows.append({'instance_id': task['id'], 'model_name_or_path': 'aster',
                         'model_patch': (root / 'prediction.patch').read_text()})
    (PRIVATE / 'aster.predictions.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in rows))
    print('Exported repository patches:', len(rows))


def summarize():
    frozen = runner.check_sources()
    rows = []
    original = {r['task']: r for r in load(runner.ORIGINAL / 'results.json') if r['agent'] == 'aster'}
    requests_seen = 0
    for task in load(PRIVATE / 'tasks.json'):
        root = PRIVATE / 'runs' / ('aster-' + task['id'])
        if not (root / 'result.json').exists():
            continue
        row = load(root / 'result.json')
        launch = load(root / 'launch.json')
        old_launch = load(runner.BASE / 'runs' / row['job'] / 'launch.json')
        assert launch['prompt_sha256'] == old_launch['prompt_sha256']
        assert launch['image_id'] == task['image_id'] and launch['seconds_limit'] == 900
        assert launch['prompt_sha256'] == hashlib.sha256(launch['prompt'].encode()).hexdigest()
        counts, requests = common.usage(root)
        for request in requests:
            # A timeout can leave a request with no response metadata; keep its unknown usage.
            request_file = root / 'provider' / (request['request_id'] + '.request.json')
            sent = load(request_file)
            assert sent['model'] == frozen['model']
            assert sent['reasoning']['effort'] == 'medium' and sent['max_output_tokens'] == 8192
            assert request.get('response_model') in (None, frozen['model'])
        requests_seen += len(requests)
        row.update(counts)
        row['final'], row['phase'], row['stop_reason'] = common.final_text(root, 'aster')
        state = load(root / 'aster-result.json') if (root / 'aster-result.json').exists() else {}
        row['runtime'] = {key: state.get(key) for key in (
            'turn', 'step', 'final_revisions', 'verification', 'verification_failures')}
        if task['kind'] == 'repository':
            score = PRIVATE / 'logs/evaluation' / RUN_ID / 'aster' / task['id']
            patch = (root / 'prediction.patch').read_bytes()
            assert hashlib.sha256(patch).hexdigest() == row['patch_sha256']
            grade = load(score / 'report.json')[task['id']] if (score / 'report.json').exists() else None
            row.update(grade=grade, graded=grade is not None or not patch,
                       passed=bool(grade and grade['resolved']),
                       grading_error=bool(grade and grade.get('infra_failure')))
        else:
            score = root / 'grading'
            row.update(graded='grade' in row, passed=row.get('grade', {}).get('passed', False),
                       grading_error=row.get('grade', {}).get('tests', 0) == 0)
        row['original_passed'] = original[task['id']]['passed']
        target = HERE / 'results' / task['id']
        save(target / 'launch.json', launch)
        save(target / 'result.json', row)
        if task['kind'] == 'repository':
            (target / 'prediction.patch').write_bytes(patch)
        if (score / 'test_output.txt').exists():
            (target / 'tests.txt.gz').write_bytes(gzip.compress((score / 'test_output.txt').read_bytes(), mtime=0))
        rows.append(row)
    summary = {
        'planned': 20, 'finished': len(rows), 'graded': sum(r['graded'] for r in rows),
        'passed': sum(r['passed'] for r in rows),
        'grading_errors': sum(r['grading_error'] for r in rows),
        'normal_delivery_and_passed': sum(r['exit_code'] == 0 and bool(r['final']) and r['passed'] for r in rows),
        'completed': sum(r['phase'] == 'completed' for r in rows),
        'completed_but_independent_failed': sum(r['phase'] == 'completed' and r['graded'] and not r['passed'] for r in rows),
        'timed_out': sum(r['timed_out'] for r in rows),
        'seconds': round(sum(r['seconds'] for r in rows), 3),
        'median_seconds': statistics.median(r['seconds'] for r in rows) if rows else None,
        **{k: sum(r[k] for r in rows) for k in ('input_tokens', 'output_tokens', 'cached_tokens',
                                             'total_tokens', 'requests', 'request_errors', 'missing_usage')},
        'by_kind': {kind: {'finished': sum(r['kind'] == kind for r in rows),
                          'passed': sum(r['kind'] == kind and r['passed'] for r in rows)}
                    for kind in ('repository', 'terminal', 'small')},
        'score_policy': frozen['score_policy'], 'evaluation_role': frozen['evaluation_role'],
    }
    summary['provider_aborts'] = [r['task'] for r in rows if r['stop_reason'] == 'provider_error']
    summary['health_probes'] = {p.name: common.usage(p)[0]
        for p in (PRIVATE / 'runs').glob('smoke-provider-health*')}
    pause = PRIVATE / 'scheduler-pause.json'
    if pause.exists():
        summary['scheduler_pause'] = load(pause)
    save(HERE / 'results.json', rows)
    save(HERE / 'summary.json', summary)
    save(HERE / 'audit.json', {'formal_episodes': len(rows), 'graded': summary['graded'],
        'source_and_runner_hashes_match': True, 'source_materials_match': True,
        'same_prompt_image_model_budget': True, 'requests': requests_seen,
        'complete': len(rows) == 20 and summary['graded'] == 20})
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('status', 'export', 'results'))
    args = parser.parse_args()
    {'status': status, 'export': export, 'results': summarize}[args.mode]()
