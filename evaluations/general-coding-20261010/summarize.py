"""Summarize preserved runs and independent grades without calling a model."""
import argparse
import gzip
import hashlib
import json
import statistics
import tarfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
PRIVATE = REPO / 'artifacts/private' / HERE.name
AGENTS = ('aster', 'pi', 'codex')


def load(path):
    return json.loads(path.read_text())


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')


def official(run, model, instance):
    path = PRIVATE / 'logs/evaluation' / run / model / instance / 'report.json'
    return load(path)[instance] if path.exists() else None


def preflight():
    rows = load(PRIVATE / 'repository-dataset.json')
    checks = []
    for row in rows:
        name = row['instance_id']
        gold = official('general-gold-20261010', 'gold', name)
        starter = official('general-starter-20261010', 'starter', name)
        complete = bool(gold and starter)
        required = {}
        for group in ('FAIL_TO_PASS', 'PASS_TO_PASS'):
            required[group] = row[group] if isinstance(row[group], list) else json.loads(row[group])
        coverage = complete and all(set(required[group]) <= set(report['tests_status'][group]['success'] +
                                   report['tests_status'][group]['failure'])
                                   for group in required for report in (gold, starter))
        ready = complete and coverage and gold['resolved'] and not starter['resolved'] and not any(
            r.get('infra_failure') for r in (gold, starter))
        checks.append({'id': name, 'ready': bool(ready), 'required_tests_observed': bool(coverage),
                       'gold': gold, 'starter': starter})
    for task in load(PRIVATE / 'tasks.json'):
        if task['kind'] != 'repository':
            path = PRIVATE / 'preflight' / task['id'] / 'preflight.json'
            checks.append(load(path) if path.exists() else {'id': task['id'], 'ready': False})
    result = {'ready': len(checks) == 20 and all(r['ready'] for r in checks), 'tasks': checks}
    save(PRIVATE / 'preflight/summary.json', result)
    save(HERE / 'preflight.json', result)
    print('Preflight:', sum(r['ready'] for r in checks), '/20')
    for row in checks:
        if not row['ready']:
            print('Not ready:', row['id'])


def usage(root):
    requests = {p.stem: load(p) for p in (root / 'provider').glob('*.json')
                if not p.name.endswith('.request.json')}
    for path in (root / 'provider').glob('*.request.json'):
        key = path.name.removesuffix('.request.json')
        if key not in requests:
            requests[key] = {'request_id': key, 'usage': None, 'error': 'metadata_missing'}
    for key, row in requests.items():
        response = root / 'provider' / (key + '.response')
        if row.get('terminal') or not response.exists():
            continue
        raw = response.read_text(errors='replace')
        if raw.lstrip().startswith('{'):
            try:
                body = json.loads(raw)
            except json.JSONDecodeError:
                continue
            # Background memory requests can return JSON instead of SSE.
            if body.get('object') == 'response':
                row['usage'] = body.get('usage') or row.get('usage')
                row['terminal'] = 'response.' + body.get('status', 'unknown')
                row['response_model'] = body.get('model')
                row['metadata_recovered_from_response'] = True
        else:
            for line in raw.splitlines():
                if not line.startswith('data: '):
                    continue
                try:
                    event = json.loads(line[6:])
                except json.JSONDecodeError:
                    continue
                if event.get('type') == 'error':
                    row['stream_error'] = {k: event.get(k) for k in ('code', 'message')}
    rows = sorted(requests.values(), key=lambda r: (r.get('started', 0), r['request_id']))
    usages = [r['usage'] for r in rows if r.get('usage')]
    return {'requests': len(rows), 'missing_usage': len(rows) - len(usages),
            'request_errors': sum(bool(r.get('error') or r.get('stream_error')) or r.get('http_status', 200) >= 400 or
                                  r.get('terminal') in {'response.failed', 'response.incomplete'} for r in rows),
            'input_tokens': sum(u.get('input_tokens', 0) for u in usages),
            'output_tokens': sum(u.get('output_tokens', 0) for u in usages),
            'cached_tokens': sum(u.get('input_tokens_details', {}).get('cached_tokens', 0) for u in usages),
            'total_tokens': sum(u.get('total_tokens', 0) for u in usages)}, rows


def final_text(root, agent):
    if agent == 'aster':
        path = root / 'aster-result.json'
        state = load(path) if path.exists() else {}
        return state.get('final', ''), state.get('phase'), state.get('stop_reason')
    texts = []
    settled = False
    for line in (root / 'agent.jsonl').read_text(errors='replace').splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if agent == 'codex':
            if event.get('type') == 'turn.completed':
                settled = True
            item = event.get('item', {})
            if event.get('type') == 'item.completed' and item.get('type') == 'agent_message':
                texts.append(item.get('text', ''))
        elif event.get('type') == 'agent_settled':
            settled = not event.get('aborted', False)
        elif event.get('type') in {'message_end', 'agent_end'}:
            messages = [event['message']] if 'message' in event else event.get('messages', [])
            for message in messages:
                if message.get('role') == 'assistant':
                    text = '\n'.join(c.get('text', '') for c in message.get('content', []) if c.get('type') == 'text')
                    # A settled error can follow tool-use progress without a final answer.
                    texts = [text] if message.get('stopReason') == 'stop' else []
    return texts[-1] if texts and settled else '', None, None


def status():
    labels = load(HERE / 'completion_claims.json')['labels']
    finished, active = [], []
    for root in sorted((PRIVATE / 'runs').glob('*')):
        if root.name.startswith('smoke-'):
            continue
        if (root / 'result.json').exists():
            result = load(root / 'result.json')
            finished.append(result)
            if root.name not in labels:
                final, phase, stop = final_text(root, result['agent'])
                print(json.dumps(dict(unlabelled=root.name, final=final, phase=phase, stop=stop), ensure_ascii=False))
        elif (root / 'launch.json').exists():
            active.append(dict(job=root.name, elapsed=round(time.time() - load(root / 'launch.json')['started'])))
    print(json.dumps(dict(finished=len(finished), active=active,
                          timeouts=[r['job'] for r in finished if r['timed_out']]), ensure_ascii=False))


def summarize():
    tasks = load(PRIVATE / 'tasks.json')
    claims = load(HERE / 'completion_claims.json')
    rows = []
    for task in tasks:
        for agent in AGENTS:
            root = PRIVATE / 'runs' / (agent + '-' + task['id'])
            if not (root / 'result.json').exists():
                continue
            row = load(root / 'result.json')
            counts, requests = usage(root)
            row.update(counts)
            row.update(source=task['source'], difficulty=task['difficulty'])
            final, phase, stop = final_text(root, agent)
            row.update(final=final, phase=phase, stop_reason=stop)
            row['completion_claim'] = claims['labels'].get(row['job'])
            if task['kind'] == 'repository':
                grade = official('general-' + agent + '-20261010', agent, task['id'])
                patch = (root / 'prediction.patch').read_bytes()
                assert hashlib.sha256(patch).hexdigest() == row['patch_sha256']
                row['graded'] = grade is not None or not patch
                row['passed'] = bool(grade and grade['resolved'])
                row['grading_error'] = bool(grade and grade.get('infra_failure'))
                row['grade'] = grade
            else:
                row['graded'] = 'grade' in row
                row['passed'] = row.get('grade', {}).get('passed', False)
                row['grading_error'] = row.get('grade', {}).get('tests', 0) == 0
            target = HERE / 'results' / agent / task['id']
            save(target / 'launch.json', load(root / 'launch.json'))
            if task['kind'] == 'repository':
                (target / 'prediction.patch').write_bytes(patch)
            if agent == 'aster':
                state_path = root / 'aster-result.json'
                state = load(state_path) if state_path.exists() else {}
                row['runtime'] = {key: state.get(key) for key in (
                    'turn', 'step', 'final_revisions', 'verification', 'verification_failures', 'feedback')}
                events = []
                if (root / '.aster.tar').exists():
                    with tarfile.open(root / '.aster.tar') as bundle:
                        for member in bundle:
                            if member.isfile() and member.name.endswith('/trace.jsonl'):
                                for line in bundle.extractfile(member):
                                    event = json.loads(line)
                                    if event['event'] in {'completion_revision', 'verifier_result', 'run_completed', 'run_stopped'} or (
                                        event['event'] == 'tool_result' and event.get('call', {}).get('name') == 'run_tests'
                                    ):
                                        events.append(line)
                (target / 'verification.jsonl.gz').write_bytes(gzip.compress(b''.join(events), mtime=0))
            save(target / 'result.json', row)
            public_requests = [{k: v for k, v in request.items() if k != 'usage'} |
                               {'usage': {k: v for k, v in (request.get('usage') or {}).items() if k != 'attribution'}}
                               for request in requests]
            save(target / 'usage.json', public_requests)
            test_output = (PRIVATE / 'logs/evaluation' / ('general-' + agent + '-20261010') / agent /
                           task['id'] / 'test_output.txt') if task['kind'] == 'repository' else root / 'grading/test_output.txt'
            if test_output.exists():
                (target / 'tests.txt.gz').write_bytes(gzip.compress(test_output.read_bytes(), mtime=0))
            rows.append(row)
    summary = {'planned': 60, 'finished': len(rows), 'graded': sum(r['graded'] for r in rows), 'agents': {}}
    for agent in AGENTS:
        group = [r for r in rows if r['agent'] == agent]
        summary['agents'][agent] = {
            'finished': len(group), 'graded': sum(r['graded'] for r in group),
            'passed': sum(r['passed'] for r in group), 'grading_errors': sum(r['grading_error'] for r in group),
            'timed_out': sum(r['timed_out'] for r in group),
            'final_answers': sum(bool(r['final']) for r in group),
            'normal_delivery_and_passed': sum(r['exit_code'] == 0 and bool(r['final']) and r['passed'] for r in group),
            'seconds': round(sum(r['seconds'] for r in group), 3),
            'median_seconds': round(statistics.median(r['seconds'] for r in group), 3) if group else None,
            **{key: sum(r[key] for r in group) for key in ('input_tokens', 'output_tokens', 'cached_tokens',
                                                         'total_tokens', 'missing_usage', 'requests', 'request_errors')},
            'by_kind': {kind: {'finished': sum(r['kind'] == kind for r in group),
                              'passed': sum(r['kind'] == kind and r['passed'] for r in group)}
                        for kind in ('repository', 'terminal', 'small')},
            'by_exposure': {label: {'finished': sum(r['previously_used'] == prior for r in group),
                                   'passed': sum(r['previously_used'] == prior and r['passed'] for r in group)}
                            for label, prior in (('new', False), ('historical', True))},
            'completion_claims': {label: {
                'count': sum((r['completion_claim'] or {}).get('label') == label for r in group),
                'graded': sum((r['completion_claim'] or {}).get('label') == label and r['graded'] for r in group),
                'failed': sum((r['completion_claim'] or {}).get('label') == label and r['graded'] and not r['passed'] for r in group),
            } for label in claims['definition']}}
    summary['smoke'] = {agent: load(PRIVATE / 'runs' / ('smoke-' + agent) / 'result.json') |
                        usage(PRIVATE / 'runs' / ('smoke-' + agent))[0] for agent in AGENTS}
    aster = [r for r in rows if r['agent'] == 'aster']
    summary['aster_runtime'] = {
        'completed': sum(r['phase'] == 'completed' for r in aster),
        'completed_but_independent_failed': sum(r['phase'] == 'completed' and r['graded'] and not r['passed'] for r in aster),
        'completion_revisions': sum(r['runtime']['final_revisions'] or 0 for r in aster),
        'runs_with_completion_revision': sum(bool(r['runtime']['final_revisions']) for r in aster),
    }
    save(HERE / 'results.json', rows)
    save(HERE / 'summary.json', summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def audit():
    from evaluate import hashes, verify_frozen

    manifest = verify_frozen()
    assert hashes(PRIVATE / 'sources/terminal-tasks') | {
        'aider/' + k: v for k, v in hashes(PRIVATE / 'sources/aider-tasks').items()
    } == manifest['source_hashes']
    assert hashlib.sha256((HERE / 'preflight.json').read_bytes()).hexdigest() == manifest['preflight_sha256']
    rows = load(HERE / 'results.json')
    claims = load(HERE / 'completion_claims.json')['labels']
    assert len(rows) == 60 and len({r['job'] for r in rows}) == 60
    assert len(claims) == 60 and all(r['graded'] for r in rows)
    requests = 0
    for task in load(PRIVATE / 'tasks.json'):
        launches = []
        for agent in AGENTS:
            root = PRIVATE / 'runs' / (agent + '-' + task['id'])
            launch = load(root / 'launch.json')
            launches.append(launch)
            assert launch['source_commit'] == manifest['aster_commit']
            assert launch['runner_sha256'] == manifest['runner_hashes'][str(HERE.relative_to(REPO) / 'evaluate.py')]
            assert launch['image_id'] == task['image_id'] and launch['seconds_limit'] == 900
            assert launch['prompt_sha256'] == hashlib.sha256(launch['prompt'].encode()).hexdigest()
            _, metadata = usage(root)
            for request in metadata:
                assert request.get('model') == manifest['model'], request
                assert request['reasoning_effort'] == 'medium' and request['max_output_tokens'] == 8192
                assert request.get('response_model') in (None, manifest['model'])
            requests += len(metadata)
        assert len({r['prompt_sha256'] for r in launches}) == 1
    result = dict(formal_episodes=60, distinct_tasks=20, graded=60, requests=requests,
                  source_and_runner_hashes_match=True, source_materials_match=True,
                  same_prompt_and_image_per_task=True, same_model_and_parameters=True,
                  completion_claims_labelled=60)
    save(HERE / 'audit.json', result)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('preflight', 'results', 'status', 'audit'))
    args = parser.parse_args()
    {'preflight': preflight, 'results': summarize, 'status': status, 'audit': audit}[args.mode]()
