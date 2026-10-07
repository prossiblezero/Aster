"""Offline trace, accounting, propagation and paired-results audit (no model calls)."""
import argparse
import json
from collections import Counter, defaultdict
from statistics import median

from evaluate import HERE, HISTORY, OUT, VERSIONS, completed, digest, inputs, read, save, ten, trace


def answers(text):
    try:
        text = text.strip()
        if text.startswith('```') and text.endswith('```'):
            text = text.split('\n', 1)[1].rsplit('```', 1)[0]
        rows = json.loads(text)['answers']
        assert isinstance(rows, list) and all(isinstance(r, dict) and isinstance(r.get('id'), str) for r in rows)
        assert len({r['id'] for r in rows}) == len(rows)
        return {r['id']: r for r in rows}
    except (ValueError, KeyError, TypeError, AssertionError):
        return {}


def value(row):
    return json.dumps({key: row[key] for key in ('observed', 'regression') if key in row}, sort_keys=True)


def aggregate(results):
    summary = {}
    for condition in ('scratch', 'pressure', 'roomy'):
        group = {}
        for version in VERSIONS:
            rows = [r for r in results if r['condition'] == condition and r['version'] == version]
            group[version] = dict(n=len(rows), passed=sum(r['passed'] for r in rows),
                behavior_correct=sum(r['behavior_correct'] for r in rows), facts=8 * len(rows),
                strict_facts=sum(r['grade']['correct'] for r in rows),
                parse_errors=sum(r['grade']['parse_error'] for r in rows),
                empty_answers=sum(not r['final'].strip() for r in rows),
                delegated_episodes=sum(bool(r['workers_started']) for r in rows),
                child_runs=sum(r['workers_started'] for r in rows),
                initial_token_handoff_requests=sum(r['start']['handoff_reason'] == 'team_token_limit' for r in rows),
                tokens=sum(r['usage'].get('total_tokens', 0) for r in rows),
                incomplete_usage=sum(not ten.usage_complete(r) for r in rows),
                seconds=sum(r['seconds'] for r in rows), errors=Counter(r['error'] for r in rows if r['error']))
        pairs = []
        for old in (r for r in results if r['condition'] == condition and r['version'] == 'r5'):
            new = next((r for r in results if r['condition'] == condition and r['version'] == 'r6'
                        and (r['task'], r['prefix'], r['repeat']) == (old['task'], old['prefix'], old['repeat'])), None)
            if new:
                pairs.append((old, new))
        known = [(old, new) for old, new in pairs if ten.usage_complete(old) and ten.usage_complete(new)]
        parseable = [(old, new) for old, new in pairs if not old['grade']['parse_error'] and not new['grade']['parse_error']]
        group['paired'] = dict(n=len(pairs), wins=sum(new['passed'] > old['passed'] for old, new in pairs),
            losses=sum(new['passed'] < old['passed'] for old, new in pairs), complete_usage_pairs=len(known),
            token_ratio=sum(new['usage']['total_tokens'] for old, new in known) /
                sum(old['usage']['total_tokens'] for old, new in known) if known else None,
            token_ratio_median=median(new['usage']['total_tokens'] / old['usage']['total_tokens'] for old, new in known) if known else None,
            time_ratio_median=median(new['seconds'] / old['seconds'] for old, new in pairs) if pairs else None,
            parseable_pairs=len(parseable), parseable_facts=8 * len(parseable),
            parseable_behavior={v:sum(pair[i]['behavior_correct'] for pair in parseable) for i,v in enumerate(VERSIONS)})
        summary[condition] = group
    return summary


def analyze():
    frozen, base, _ = inputs('r5')
    for version in VERSIONS:
        assert ten.hashes(HISTORY / version / 'src') == frozen['sources'][version]
    gold = {r['task']: r for r in read(OUT / 'oracle.json')}
    records, diagnostics, fingerprints, started = [], [], {}, []
    event_count = run_count = reply_count = 0
    for index, planned in enumerate(frozen['order']):
        output = OUT / 'runs' / f'{index:02d}'
        if not (output / 'result.json').exists():
            continue
        result = completed(index, planned)
        records.append(result)
        if planned['condition'] == 'pressure':
            start = result['start']
            assert start['child_tools'] is not None, 'pressure_request_not_reached'
            assert start['child_estimate'] + start['reserve'] <= planned['limit']
            if planned['version'] == 'r5':
                assert planned['limit'] < 2 * start['child_estimate'] + start['reserve']
                assert start['child_tools'] and not start['handoff_reason']
            else:
                assert not start['child_tools'] and start['handoff_reason'] == 'team_token_limit'
        root = OUT / 'workspaces' / f'{index:02d}'
        for name in ('result.json', 'launch.json', 'start.json', 'request-ledger.json'):
            path = output / name
            if path.exists():
                fingerprints[str(path.relative_to(OUT))] = digest(path)
        ledger = read(output / 'request-ledger.json') if (output / 'request-ledger.json').exists() else []
        usage = Counter()
        for item in ledger:
            usage.update(item.get('usage', {}))
        assert dict(usage) == result['usage'] == result['ledger_usage']
        assert len(ledger) == result['model_calls'] and result['usage_matches']
        if ledger:
            started.append(ledger[0]['started_seconds'])
        expected = gold[planned['task']]
        assert base.ledger_driver.product_hashes(root) == expected['public_hashes']
        assert ten.grade(base, result['final'], expected['gold'], planned['task'].endswith('-review')) == result['grade']
        assert {p.parent.name for p in (root / '.aster/runs').glob('*/checkpoint.json')} == {r['run_id'] for r in result['runs']}
        parent_rows, child_rows = answers(result['final']), defaultdict(list)
        delivered, child_details, all_events = set(), [], {}
        checkpoint_usage = Counter()
        assert sum(not r['parent_run_id'] for r in result['runs']) == 1
        for run in result['runs']:
            path = root / '.aster/runs' / run['run_id'] / 'trace.jsonl'
            checkpoint = path.with_name('checkpoint.json')
            state = read(checkpoint)['state']
            assert {key: state.get(key) for key in run} == run
            checkpoint_usage.update(state['usage'])
            if not run['parent_run_id']:
                assert state['final'] == result['final'] and state['phase'] == result['phase']
            fingerprints[str(checkpoint.relative_to(OUT))] = digest(checkpoint)
            fingerprints[str(path.relative_to(OUT))] = digest(path)
            events = trace(path)
            run_count += 1
            event_count += len(events)
            reply_count += sum(e['event'] == 'model_reply' for e in events)
            all_events[run['run_id']] = events
            if not run['parent_run_id']:
                for event in events:
                    if event['event'] == 'worker_handoff_delivered':
                        delivered.add(event['child_run_id'])
                    if event['event'] == 'tool_result' and event['call']['name'] == 'worker_status':
                        data = event['outcome'].get('data', {})
                        delivered.update(item['run_id'] for item in data.get('workers', [data]) if item.get('delivery'))
                continue
            handoff_path = path.with_name('handoff.json')
            fingerprints[str(handoff_path.relative_to(OUT))] = digest(handoff_path)
            handoff = read(handoff_path)
            assert handoff['answer'] == state['final'] and handoff['status'] == state['phase']
            rows = answers(handoff['answer'])
            for key, row in rows.items():
                child_rows[key].append(row)
            contexts = {}
            for event in events:
                if event['event'] == 'context_built':
                    request_path = root / '.aster' / event['request_artifact']
                    fingerprints[str(request_path.relative_to(OUT))] = digest(request_path)
                    contexts[event['request_sha256']] = read(request_path)['payload']
            replies = [e for e in events if e['event'] == 'model_reply']
            assert all(e['request_sha256'] in contexts for e in replies)
            tool_free = sum(not contexts[e['request_sha256']].get('tools') for e in replies)
            model_conclusion = any(e['text'] and e['text'] == handoff['answer'] for e in replies)
            model_conclusion |= any(e['event'] == 'tool_result' and e['call']['name'] == 'worker_finish'
                                    and e['outcome']['status'] == 'success' for e in events)
            child_details.append(dict(run_id=run['run_id'], delivery=handoff['delivery'],
                stop_reason=run['stop_reason'], model_conclusion=bool(model_conclusion),
                parseable_facts=len(rows), tool_free_replies=tool_free,
                behavioral_correct=sum(key in rows and value(rows[key]) == value(g) for g in expected['gold'] for key in [g['id']])))
        assert dict(checkpoint_usage) == result['usage']
        for detail in child_details:
            detail['received_by_parent'] = detail['run_id'] in delivered
        if planned['prefix']:
            prefix = next(p for p in read(HERE / 'PREFIXES.json') if p['id'] == planned['prefix'])
            initial_child = next(e['child_run_id'] for events in all_events.values() for e in events if e['event'] == 'worker_started')
            for run in result['runs']:
                if run['parent_run_id'] and run['run_id'] != initial_child:
                    continue
                role = 'child' if run['parent_run_id'] else 'parent'
                reads = [e['outcome']['data'] for e in all_events[run['run_id']]
                         if e['event'] == 'tool_result' and e['call']['name'] == 'read_file'
                         and e['outcome']['status'] == 'success'][:len(prefix[role])]
                assert [{key: data[key] for key in item} for data, item in zip(reads, prefix[role], strict=True)] == prefix[role]
        propagation = Counter()
        for g in expected['gold']:
            key = g['id']
            reports = child_rows.get(key, [])
            if len(reports) != 1:
                propagation['multiple_child_reports' if reports else 'no_parseable_child_fact'] += 1
                continue
            child_correct = value(reports[0]) == value(g)
            parent_correct = key in parent_rows and value(parent_rows[key]) == value(g)
            if not parent_rows:
                propagation['parent_output_unscorable'] += 1
            elif child_correct:
                propagation['child_correct_preserved' if parent_correct else 'parent_lost_correct_fact'] += 1
            else:
                propagation['parent_repaired_child_error' if parent_correct else 'both_wrong'] += 1
        diagnostics.append(planned | dict(index=index, children=child_details, propagation=dict(propagation)))
    assert started == sorted(started)
    summary = aggregate(records)
    for condition, groups in summary.items():
        for version in VERSIONS:
            rows = [d for d in diagnostics if d['condition'] == condition and d['version'] == version]
            details = [c for row in rows for c in row['children']]
            propagation = Counter()
            for row in rows:
                propagation.update(row['propagation'])
            groups[version].update(model_handoffs=sum(c['model_conclusion'] for c in details),
                token_partial_model_handoffs=sum(c['model_conclusion'] and c['stop_reason'] == 'team_token_limit' for c in details),
                fallback_handoffs=sum(not c['model_conclusion'] for c in details),
                handoffs_received=sum(c['received_by_parent'] for c in details),
                partial_handoffs=sum(c['delivery'] == 'partial' for c in details),
                tool_free_child_replies=sum(c['tool_free_replies'] for c in details),
                child_behavior_correct=sum(c['behavioral_correct'] for c in details), propagation=dict(propagation))
    save(HERE / 'results.json', records)
    save(HERE / 'summary.json', summary)
    save(HERE / 'diagnostics.json', diagnostics)
    save(HERE / 'TRACE-HASHES.json', fingerprints)
    save(HERE / 'AUDIT.json', dict(complete=len(records) == len(frozen['order']), episodes=len(records),
        artifacts=len(fingerprints), runs=run_count, events=event_count, model_replies=reply_count,
        freeze_sha256=digest(OUT / 'freeze.json'),
        analyzer_sha256=digest(HERE / 'analyze.py'), traces_and_usage_and_grades_verified=True))
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--progress', action='store_true')
    if parser.parse_args().progress:
        rows = [read(p) for p in sorted((OUT / 'runs').glob('*/result.json'))]
        print(json.dumps(dict(completed=len(rows), summary=aggregate(rows)), ensure_ascii=False, indent=2))
    else:
        analyze()
