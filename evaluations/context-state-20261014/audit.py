"""Read saved attempts and validate the frozen Stage 4 acceptance criteria offline."""

import hashlib
import json
import statistics
import tarfile
from collections import Counter, defaultdict
from datetime import datetime

from evaluate import BASE, BASE_HERE, HERE, PRIVATE, V9_HERE, imported, load, save, verify
from metrics import canon, percentile, stable


def fingerprint(payload):
    return hashlib.sha256(canon({k: payload.get(k) for k in (
        'input', 'tools', 'prompt_cache_key')}).encode()).hexdigest()


def trace_audit(root, foreground):
    contexts, replies, snapshots = [], [], defaultdict(list)
    with tarfile.open(root / '.aster.tar') as bundle:
        for member in bundle.getmembers():
            if member.isfile() and member.name.endswith('/trace.jsonl'):
                for line in bundle.extractfile(member):
                    event = json.loads(line)
                    if event['event'] == 'context_built':
                        contexts.append({k: event[k] for k in (
                            'seq', 'time', 'request_sha256', 'request_artifact',
                            'estimated_tokens', 'estimate_basis', 'compacted',
                            'prefix_drift', 'first_changed_item', 'masked_outputs',
                            'sections', 'policy_version')})
                        contexts[-1]['context_notes'] = event.get('context_notes', 0)
                    elif event['event'] == 'model_reply':
                        replies.append(event)
        for context in contexts:
            payload = json.load(bundle.extractfile('.aster/' + context['request_artifact']))['payload']
            assert hashlib.sha256(canon(payload).encode()).hexdigest() == context['request_sha256']
            snapshots[fingerprint(payload)].append(context)

    by_hash = {c['request_sha256']: c for c in contexts}
    receipt_rows = []
    for reply in replies:
        context = by_hash[reply['request_sha256']]
        diagnostic = reply.get('diagnostics') or {}
        actual = (diagnostic.get('usage') or {}).get('input_tokens', 0)
        if (context['estimate_basis'] == 'receipt' and actual
                and diagnostic.get('classification') == 'success'
                and not diagnostic.get('flags')):
            receipt_rows.append({
                'seq': context['seq'], 'request_sha256': context['request_sha256'],
                'estimated': context['estimated_tokens'], 'actual': actual,
                'ratio': context['estimated_tokens'] / actual,
                'compacted': context['compacted'], 'attempt': diagnostic.get('attempt'),
            })

    previous, previous_tokens, rewrites, unmatched, low_hits = None, 0, [], [], []
    strict_rewrites = []
    previous_sent, all_sent_rewrites = None, []
    for metadata, payload in foreground:
        candidates = [c for c in snapshots[fingerprint(payload)]
                      if datetime.fromisoformat(c['time']).timestamp() <= metadata.get('started', 0)]
        context = max(candidates, key=lambda c: c['time']) if candidates else None
        if context is None:
            unmatched.append(metadata['request_id'])
        usage = metadata.get('usage') or {}
        if previous_sent is not None and not stable(previous_sent['input'], payload['input'], strict=True):
            all_sent_rewrites.append({
                'request_id': metadata['request_id'],
                'context_seq': context['seq'] if context else None,
                'compacted': context['compacted'] if context else None,
                'has_usage': bool(usage.get('input_tokens')),
            })
        previous_sent = payload
        if not usage.get('input_tokens'):
            continue
        if previous is not None and not stable(previous['input'], payload['input'], strict=True):
            strict_rewrites.append({
                'request_id': metadata['request_id'],
                'context_seq': context['seq'] if context else None,
                'compacted': context['compacted'] if context else None,
            })
        if previous is not None and not stable(previous['input'], payload['input']):
            rewrites.append({
                'request_id': metadata['request_id'],
                'context_seq': context['seq'] if context else None,
                'compacted': context['compacted'] if context else None,
                'uncached': usage['input_tokens'] - usage.get('input_tokens_details', {}).get('cached_tokens', 0),
            })
        elif previous is not None:
            cached = usage.get('input_tokens_details', {}).get('cached_tokens', 0)
            if cached / previous_tokens < .85:
                low_hits.append({
                    'request_id': metadata['request_id'], 'previous_input': previous_tokens,
                    'input': usage['input_tokens'], 'cached': cached,
                    'same_tools': previous['tools'] == payload['tools'],
                    'same_cache_key': previous['prompt_cache_key'] == payload['prompt_cache_key'],
                    'ordered_full_prefix_identical': len(payload['input']) >= len(previous['input']) and all(
                        json.dumps(a) == json.dumps(b)
                        for a, b in zip(previous['input'], payload['input'], strict=False)),
                })
        previous = payload
        previous_tokens = usage['input_tokens']
    return {'contexts': contexts, 'receipt_estimates': receipt_rows,
            'rewrites': rewrites, 'unmatched_foreground_requests': unmatched,
            'strict_rewrites': strict_rewrites,
            'all_sent_rewrites': all_sent_rewrites, 'stable_hit_below_85_percent': low_hits}


def main():
    manifest = verify()
    rows, cache = load(HERE / 'results.json'), load(HERE / 'metrics.json')['agents']['aster']
    assert len(rows) == len({r['task'] for r in rows}) == 20
    assert {r['task'] for r in rows} == set(manifest['tasks'])
    assert all(r['graded'] for r in rows)
    assert load(BASE_HERE / 'environment-preflight.json')['ready']
    helpers = imported('context_state_audit_usage', HERE.parent / 'general-coding-20261010/summarize.py')
    baseline = {r['task']: r for r in load(BASE_HERE / 'results.json') if r['agent'] == 'aster'}
    v9 = {r['task']: r for r in load(V9_HERE / 'results.json')}
    traces, request_ids, faults, background, no_test_status = {}, set(), [], [], []
    checked_hashes = 0
    for row in rows:
        root = PRIVATE / 'runs' / row['job']
        launch = load(root / 'launch.json')
        assert hashlib.sha256(launch['prompt'].encode()).hexdigest() == launch['prompt_sha256']
        for agent in ('aster', 'pi', 'codex'):
            old = load(BASE / 'runs' / (agent + '-' + row['task']) / 'launch.json')
            assert all(launch[k] == old[k] for k in (
                'prompt_sha256', 'image_id', 'model', 'seconds_limit', 'runner_sha256'))
        usage, requests = helpers.usage(root)
        assert all(usage[k] == row[k] for k in usage)
        foreground = []
        for request in requests:
            ident = request['request_id']
            assert ident not in request_ids
            request_ids.add(ident)
            raw = (root / 'provider' / (ident + '.request.json')).read_bytes()
            if request.get('request_sha256'):
                assert hashlib.sha256(raw).hexdigest() == request['request_sha256']
                checked_hashes += 1
            payload = json.loads(raw)
            assert (payload['model'], payload['reasoning']['effort'], payload['max_output_tokens']) == (
                'gpt-5.6-luna', 'medium', 8192)
            memory = [t.get('name') for t in payload.get('tools', [])] == ['memory_changes']
            if memory:
                assert payload['prompt_cache_key'].startswith('memory-')
                background.append(request)
            else:
                assert payload.get('prompt_cache_key')
                foreground.append((request, payload))
            if (request.get('error') or request.get('stream_error')
                    or request.get('http_status', 200) >= 400
                    or request.get('terminal') in ('response.failed', 'response.incomplete')):
                faults.append({'task': row['task'], 'background': memory, **{
                    k: request.get(k) for k in ('request_id', 'http_status', 'error', 'terminal', 'stream_error')}})
        assert len({p['prompt_cache_key'] for _, p in foreground}) == 1
        traces[row['task']] = trace_audit(root, foreground)
        if row['kind'] == 'repository':
            assert hashlib.sha256((root / 'prediction.patch').read_bytes()).hexdigest() == row['patch_sha256']
            if not row['grade'].get('tests_status'):
                no_test_status.append(row['task'])
        else:
            if not row['grade'].get('tests'):
                no_test_status.append(row['task'])
            if row['passed']:
                assert row['grade']['tests'] == row['grade']['expected_tests']

    contexts = [c for trace in traces.values() for c in trace['contexts']]
    assert all(c['policy_version'] == 'deterministic-v10' for c in contexts)
    estimates = [r for trace in traces.values() for r in trace['receipt_estimates']]
    ratios = [r['ratio'] for r in estimates]
    rewrites = [r for trace in traces.values() for r in trace['rewrites']]
    all_sent_rewrites = [r for trace in traces.values() for r in trace['all_sent_rewrites']]
    unmatched = [r for trace in traces.values() for r in trace['unmatched_foreground_requests']]
    assert len(rewrites) == cache['rewrites']
    assert sum(len(t['strict_rewrites']) for t in traces.values()) == cache['strict_rewrites']
    assert sum(len(t['stable_hit_below_85_percent']) for t in traces.values()) == cache['low_hits']
    assert sum(r['uncached'] for r in rewrites) == cache['rewrite_uncached']
    assert len(request_ids) == sum(r['requests'] for r in rows)
    median, p95 = statistics.median(ratios) if ratios else None, percentile(ratios, .95)
    stable_requests = cache['followups'] - cache['rewrites']
    measurements = [
        ('strict_rewrite_fraction', cache['strict_rewrites'] / cache['followups'] if cache['followups'] else None, '<=', .03),
        ('prefix_drift', sum(c['prefix_drift'] for c in contexts), '<=', 0),
        ('rewrite_without_compaction', sum(not r['compacted'] for r in all_sent_rewrites), '<=', 0),
        ('unmatched_foreground_requests', len(unmatched), '<=', 0),
        ('stable_hit_mean', cache['stable_hit_mean'], '>=', .88),
        ('low_hit_fraction', cache['low_hits'] / stable_requests if stable_requests else None, '<=', .15),
        ('receipt_estimate_median', median, '<=', 1.02),
        ('receipt_estimate_p95', p95, '<=', 1.05),
        ('cache_ratio', cache['cache_ratio'], '>=', .85),
        ('uncached', cache['uncached'], '<=', 1300000),
        ('input', cache['input'], '<=', 10043357),
        ('independent_passes', sum(r['passed'] for r in rows), '>=', 15),
    ]
    gates = [{'metric': name, 'actual': actual, 'operator': operator, 'threshold': threshold,
              'passed': actual is not None and (actual <= threshold if operator == '<=' else actual >= threshold)}
             for name, actual, operator, threshold in measurements]
    report = {
        'all_20_unique_attempts_scored': True, 'frozen_source_and_inputs_verified': True,
        'same_prompts_images_models_deadlines_and_runner': True,
        'provider_requests': len(request_ids), 'provider_request_hashes_checked': checked_hashes,
        'faults': faults, 'zero_test_status': no_test_status,
        'completion': {
            'completed': sum(r['phase'] == 'completed' for r in rows),
            'completed_but_failed': [r['task'] for r in rows if r['phase'] == 'completed' and not r['passed']],
            'passed_without_normal_delivery': [r['task'] for r in rows if r['passed'] and not r['normal_delivery_and_passed']],
        },
        'pass_flips': {
            'regressions': [r['task'] for r in rows if baseline[r['task']]['passed'] and not r['passed']],
            'improvements': [r['task'] for r in rows if not baseline[r['task']]['passed'] and r['passed']],
        },
        'pass_flips_from_v9': {
            'regressions': [r['task'] for r in rows if v9[r['task']]['passed'] and not r['passed']],
            'improvements': [r['task'] for r in rows if not v9[r['task']]['passed'] and r['passed']],
        },
        'background_included': {
            'requests': len(background), 'missing_usage': sum(not r.get('usage') for r in background),
            'input_tokens': sum((r.get('usage') or {}).get('input_tokens', 0) for r in background),
            'output_tokens': sum((r.get('usage') or {}).get('output_tokens', 0) for r in background),
            'cached_tokens': sum((r.get('usage') or {}).get('input_tokens_details', {}).get('cached_tokens', 0) for r in background),
        },
        'context_events': len(contexts), 'estimate_bases': dict(Counter(c['estimate_basis'] for c in contexts)),
        'context_notes': sum(c['context_notes'] for c in contexts),
        'context_note_tokens': sum(c['sections']['context_notes'] for c in contexts),
        'foreground_requests': cache['requests'],
        'mean_input_items': cache['mean_input_items'], 'mean_input_tokens': cache['mean_input_tokens'],
        'compactions': sum(c['compacted'] for c in contexts),
        'sent_rewrites_including_missing_usage': len(all_sent_rewrites),
        'masked_outputs': sum(c['masked_outputs'] for c in contexts),
        'receipt_estimates': {'count': len(ratios), 'median': median, 'p95': p95,
                             'minimum': min(ratios, default=None), 'maximum': max(ratios, default=None)},
        'gates': gates, 'all_gates_passed': all(g['passed'] for g in gates), 'tasks': traces,
    }
    save(HERE / 'audit.json', report)
    lines = ['# Stage 4 验收', '', '离线读取单次尝试的原始证据；不调用模型、不修改成绩。', '',
             '| 指标 | 实测 | 门槛 | 达标 |', '|---|---:|---:|:---:|']
    for gate in gates:
        actual = gate['actual']
        value = f'{actual:.6f}' if isinstance(actual, float) else str(actual)
        lines.append(f"| {gate['metric']} | {value} | {gate['operator']} {gate['threshold']} | "
                     + ('是' if gate['passed'] else '否') + ' |')
    lines += ['', f"回执估算 {len(ratios)} 次；压缩 {report['compactions']} 次；掩码 {report['masked_outputs']} 项。",
              '', '细节、逐题轨迹关联及请求异常见 [audit.json](audit.json)。', '']
    (HERE / 'ACCEPTANCE.md').write_text('\n'.join(lines))
    print(json.dumps({k: v for k, v in report.items() if k != 'tasks'}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
