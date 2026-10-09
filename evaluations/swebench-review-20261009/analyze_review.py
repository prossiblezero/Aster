"""Measure work after the optional review; this is not a causal cost ablation."""
from __future__ import annotations

import json
import tarfile
from collections import Counter
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
PRIVATE = HERE.parents[1] / 'artifacts/private' / HERE.name


def epoch(event):
    return datetime.fromisoformat(event['time']).timestamp()


def main():
    rows = []
    errors = []
    for result in json.loads((HERE / 'aster.results.json').read_text()):
        instance = result['instance_id']
        with tarfile.open(PRIVATE / 'runs' / ('aster-' + instance) / '.aster.tar') as archive:
            events = []
            for member in archive.getmembers():
                if member.isfile() and member.name.endswith('/trace.jsonl'):
                    events.extend(json.loads(line) for line in archive.extractfile(member))
        events.sort(key=lambda e: (e['time'], e['seq']))
        reviews = [e for e in events if e['event'] == 'patch_review_requested']
        assert len(reviews) <= 1, instance
        for event in events:
            if event['event'] == 'provider_error':
                error = json.loads(event['error'])
                errors.append({'instance_id': instance, **{
                    key: error.get(key) for key in ('category', 'code', 'attempts', 'status', 'message')
                }})
        row = {'instance_id': instance, 'requested': bool(reviews), 'resolved': result['resolved']}
        if reviews:
            start = epoch(reviews[0])
            end_events = [e for e in events if e['event'] in {'run_completed', 'run_stopped'}]
            end = epoch(end_events[0]) if end_events else float('inf')
            tools = [e for e in events if e['event'] == 'tool_result' and start <= epoch(e) <= end]
            calls = json.loads((HERE / 'results/aster' / (instance + '.usage.json')).read_text())
            calls = [r for r in calls if start <= r.get('started', 0) <= end]
            row.update(
                review_seconds_since_run_start=round(start - epoch(events[0]), 3),
                requests_after_review=len(calls),
                known_tokens_after_review=sum((r.get('usage') or {}).get('total_tokens', 0) for r in calls),
                requests_without_usage_after_review=sum(not r.get('usage') for r in calls),
                tools_after_review=dict(Counter(e['call']['name'] for e in tools)),
                changed_paths_after_review=sorted({p for e in tools for p in e['outcome']['changed_paths']}),
                verification_after_review=[{'command': e['call']['arguments'].get('command'),
                    'status': e['outcome']['status'], 'reason': e['outcome']['reason']}
                    for e in tools if e['call']['name'] == 'run_tests'],
            )
        rows.append(row)
    summary = {'attempts': len(rows), 'reviews_requested': sum(r['requested'] for r in rows),
               'reviews_with_edits': sum(bool(r.get('changed_paths_after_review')) for r in rows),
               'known_tokens_after_review': sum(r.get('known_tokens_after_review', 0) for r in rows),
               'requests_after_review': sum(r.get('requests_after_review', 0) for r in rows),
               'note': 'Requests from review until foreground completion, or timeout. No paired ablation; these totals are not a measured net cost increase.',
               'tasks': rows}
    (HERE / 'review-analysis.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
    (HERE / 'service-errors.json').write_text(json.dumps(errors, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({key: value for key, value in summary.items() if key not in {'tasks', 'note'}}))


if __name__ == '__main__':
    main()
