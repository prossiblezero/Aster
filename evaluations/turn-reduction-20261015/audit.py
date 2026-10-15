"""Offline quality, cost, provenance and explicit-declaration checks for each frozen round."""
import argparse
import hashlib
import json
import tarfile
from collections import defaultdict
from datetime import datetime

from evaluate import (
    BASE,
    HERE,
    STABLE_FAIL,
    STABLE_PASS,
    VARIABLE,
    imported,
    load,
    paths,
    save,
    verify,
)
from metrics import canon, stable


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


def quality(rows):
    by = {(r['task'], r['attempt']): r for r in rows}
    assert len(by) == len(rows), 'Duplicate task attempts'
    required = {(t, 1) for t in STABLE_PASS + STABLE_FAIL + VARIABLE}
    required |= {(t, a) for t in VARIABLE for a in (2, 3)}
    complete = required <= by.keys() and all(by[key]['graded'] for key in required)
    failed = [t for t in STABLE_PASS if not by.get((t, 1), {}).get('passed')]
    rescue = len(failed) == 1 and all(by.get((failed[0], a), {}).get('graded')
                                     and by[(failed[0], a)]['passed'] for a in (2, 3))
    allowed = required | ({(failed[0], a) for a in (2, 3)} if len(failed) == 1 else set())
    return {'all_planned_scored': complete, 'allowed_attempts_only': by.keys() <= allowed,
            'stable_pass': not failed or rescue, 'stable_first_passes': 10 - len(failed),
            'stable_rescued': failed if rescue else [],
            'variable_passes': sum(bool(by.get((t, a), {}).get('passed')) for t in VARIABLE for a in (1, 2, 3))}


def declarations(root):
    baseline, accepted = {}, []
    with tarfile.open(root / '.aster.tar') as bundle:
        for member in bundle:
            if not member.isfile() or not member.name.endswith('/trace.jsonl'):
                continue
            for line in bundle.extractfile(member):
                event = json.loads(line)
                if event.get('event') != 'tool_result' or event['call']['name'] != 'run_tests':
                    continue
                outcome = event['outcome']
                data = outcome.get('data', {})
                for name, status in data.get('baseline_results', {}).items():
                    baseline[name] = {'status': status, 'evidence_id': outcome.get('evidence_id'),
                                      'check': data.get('baseline_check'), 'seq': event['seq']}
                for name in data.get('accepted_preexisting', []):
                    accepted.append({'id': name, 'seq': event['seq'], 'command': data.get('command'),
                                     'baseline': baseline.get(name)})
    return accepted


def audit(round_no):
    manifest = verify(round_no)
    rows = [r for a in (1, 2, 3) if (paths(round_no, a)[0] / 'results.json').exists()
            for r in load(paths(round_no, a)[0] / 'results.json')]
    q = quality(rows)
    first = paths(round_no, 1)[0]
    cache = load(first / 'metrics.json')['agents'].get('aster', {})
    turns = load(first / 'turns.json')
    helpers = imported('turn_reduction_audit_usage', HERE.parent / 'general-coding-20261010/summarize.py')
    faults, accepted, traces, evidence_errors = [], [], {}, []
    request_ids = set()
    for row in rows:
        root = paths(round_no, row['attempt'])[1] / 'runs' / row['job']
        label = f"{row['attempt']}:{row['task']}"
        launch = load(root / 'launch.json')
        old = load(BASE / 'runs' / row['job'] / 'launch.json')
        assert hashlib.sha256(launch['prompt'].encode()).hexdigest() == launch['prompt_sha256']
        assert all(launch[k] == old[k] for k in ('prompt_sha256', 'image_id', 'model', 'seconds_limit', 'runner_sha256'))
        assert launch['source_commit'] == manifest['aster_commit']
        usage, records = helpers.usage(root)
        assert all(row[k] == value for k, value in usage.items())
        foreground = []
        for record in records:
            ident = record['request_id']
            assert ident not in request_ids
            request_ids.add(ident)
            raw = (root / 'provider' / (ident + '.request.json')).read_bytes()
            if record.get('request_sha256'):
                assert hashlib.sha256(raw).hexdigest() == record['request_sha256']
            payload = json.loads(raw)
            assert (payload['model'], payload['reasoning']['effort'], payload['max_output_tokens']) == ('gpt-5.6-luna', 'medium', 8192)
            memory = [t.get('name') for t in payload.get('tools', [])] == ['memory_changes']
            if not memory:
                assert payload.get('prompt_cache_key')
                foreground.append((record, payload))
            if (record.get('error') or record.get('stream_error') or not record.get('usage')
                    or record.get('http_status', 200) >= 400
                    or record.get('terminal') in ('response.failed', 'response.incomplete')):
                faults.append({'task': row['task'], 'attempt': row['attempt'], 'background': memory,
                               'missing_usage': not record.get('usage'), **{k: record.get(k) for k in
                               ('request_id', 'http_status', 'error', 'terminal', 'stream_error')}})
        if row['kind'] == 'repository':
            assert hashlib.sha256((root / 'prediction.patch').read_bytes()).hexdigest() == row['patch_sha256']
        elif row['passed']:
            assert row['grade']['tests'] == row['grade']['expected_tests']
        try:
            trace = trace_audit(root, foreground)
            traces[label] = trace
            if (trace['unmatched_foreground_requests']
                    or any(not r['compacted'] for r in trace['all_sent_rewrites'])
                    or any(c['prefix_drift'] or c['policy_version'] != 'deterministic-v10' for c in trace['contexts'])):
                evidence_errors.append({'task': label, 'reason': 'context provenance mismatch'})
            for item in declarations(root):
                item.update(task=row['task'], attempt=row['attempt'], review_id=f"{label}:{item['seq']}:{item['id']}")
                accepted.append(item)
        except (OSError, KeyError, ValueError, tarfile.TarError) as exc:
            evidence_errors.append({'task': label, 'reason': str(exc)})
    gates = []

    def gate(name, actual, operator, threshold):
        passed = actual is not None and {'==': lambda: actual == threshold,
                    '<=': lambda: actual <= threshold, '>=': lambda: actual >= threshold}[operator]()
        gates.append(dict(metric=name, actual=actual, operator=operator, threshold=threshold, passed=passed))

    gate('all_32_planned_attempts_scored', q['all_planned_scored'], '==', True)
    gate('allowed_attempts_only', q['allowed_attempts_only'], '==', True)
    gate('stable_pass_with_conditional_rescue', q['stable_pass'], '==', True)
    gate('variable_passes_of_18', q['variable_passes'], '>=', 8)
    quality_passed = all(g['passed'] for g in gates)
    gate('evidence_errors', len(evidence_errors), '==', 0)
    if round_no == 1:
        gate('fresh_read_blocked', turns.get('fresh_read_blocked'), '<=', 2)
        gate('reread_only_turns', turns.get('reread_only_turns'), '<=', 5)
        gate('turns', turns.get('turns'), '<=', 325)
        gate('cache_ratio', cache.get('cache_ratio'), '>=', .85)
        ratio = cache.get('strict_rewrites', 0) / cache['followups'] if cache.get('followups') else None
        gate('strict_rewrites_per_followup', ratio, '<=', .03)
    else:
        aider = sum(item['task'] in {'aider-simple-linked-list', 'aider-sgf-parsing'} for item in accepted)
        gate('aider_preexisting_declarations', aider, '==', 0)
        quality_passed &= aider == 0
        tasks = turns.get('tasks', {})
        requests_tasks = {'psf__requests-2931', 'psf__requests-5414'}
        during = sum(tasks[t]['turns_while_failing'] for t in requests_tasks) if requests_tasks <= tasks.keys() else None
        gate('requests_turns_while_failing', during, '<=', 8)
        previous = load(HERE / 'audit-round1.json')
        gate('turns', turns.get('turns'), '<=', previous['turn_metrics']['turns'])
        review_path = HERE / 'preexisting-review-round2.json'
        reviews = load(review_path) if review_path.exists() else {}
        reviewed = all((item.get('baseline') or {}).get('status') == 'failed'
                       and reviews.get(item['review_id'], {}).get('unrelated') is True
                       and bool(reviews[item['review_id']].get('reason')) for item in accepted)
        gate('every_declaration_reviewed_as_unrelated', reviewed, '==', True)
    report = {'round': round_no, 'tag': manifest['tag'], 'commit': manifest['aster_commit'],
              'quality': q, 'quality_passed': quality_passed, 'gates': gates,
              'all_gates_passed': all(g['passed'] for g in gates), 'cache': cache, 'turn_metrics': turns,
              'faults': faults, 'accepted_preexisting': accepted, 'evidence_errors': evidence_errors,
              'attempts': rows, 'traces': traces, 'provider_requests_all_attempts': len(request_ids)}
    save(HERE / f'audit-round{round_no}.json', report)
    lines = ['# v11 第一轮（Stage 1 + 2）' if round_no == 1 else '# v11 第二轮（Stage 1 + 2 + 3）', '',
             f"源码：`{manifest['tag']}` / `{manifest['aster_commit']}`。成本仅取 attempt 1 的 old20；重复只用于质量判断。", '',
             '| 指标 | 实测 | 门槛 | 达标 |', '|---|---:|---:|:---:|']
    for g in gates:
        lines.append(f"| {g['metric']} | {g['actual']} | {g['operator']} {g['threshold']} | {'是' if g['passed'] else '否'} |")
    baseline = load(HERE / 'baseline-turns.json')
    lines += ['', '| 轮次指标 | v10 | 本轮 attempt 1 |', '|---|---:|---:|']
    for key, value in baseline.items():
        if isinstance(value, int):
            lines.append(f'| {key} | {value} | {turns.get(key)} |')
    lines += ['', f"前台有用量请求：{cache.get('requests_with_usage')}；搜索轮：{turns.get('search_only_turns')}；"
              f"加权成本：{cache.get('weighted_cost')}（相对估算，非账单）。", '',
              '| 题目 | 分组 | attempt 1 | attempt 2 | attempt 3 |', '|---|---|---|---|---|']
    by = {(r['task'], r['attempt']): r for r in rows}
    for group, names in [('恒过', STABLE_PASS), ('恒败', STABLE_FAIL), ('波动', VARIABLE)]:
        for name in names:
            values = ['未运行' if (name, a) not in by else '未评分' if not by[(name, a)]['graded']
                      else '通过' if by[(name, a)]['passed'] else '失败' for a in (1, 2, 3)]
            lines.append(f"| {name} | {group} | {' | '.join(values)} |")
    lines += ['', '## 接受的 pre-existing 声明', '']
    for item in accepted:
        lines.append(f"- {item['review_id']}：`{item['command']}`；基线证据 `{json.dumps(item['baseline'], ensure_ascii=False)}`")
    if not accepted:
        lines.append('无。')
    lines += ['', '## 请求异常（全部尝试，包含缺用量）', '']
    lines += ['- ' + json.dumps(fault, ensure_ascii=False) for fault in faults] or ['无。']
    lines += ['', f"完整证据与累计请求：`audit-round{round_no}.json`。",
              '第二轮质量失败时按协议 revert Stage 3；第一轮失败停止，由用户决定是否回滚。', '']
    (HERE / f'REPORT-round{round_no}.md').write_text('\n'.join(lines))
    print(json.dumps({k: v for k, v in report.items() if k in {'round', 'quality', 'quality_passed', 'gates', 'all_gates_passed'}}, indent=2))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--round', type=int, choices=[1, 2], required=True)
    audit(parser.parse_args().round)
