"""Price all incurred requests, including excluded tasks and the unscored smoke.

Explicit run names only: never discovers or opens unseen heldout runs.
"""
import argparse
from datetime import datetime, timezone

from common import HERE, PRIVATE, load, run_rows, save


def charge(usage, rates):
    cached = usage.get('cached_tokens', usage.get('cache_read_input_tokens', 0))
    return ((usage.get('input_tokens', 0) - cached) * rates['uncached_input']
            + cached * rates['cached_input'] + usage.get('output_tokens', 0) * rates['output']) / 1e6


def peak(stamp):
    date = datetime.fromtimestamp(stamp, timezone.utc)
    # The experiment runs in November, with no Chinese statutory public holidays.
    assert date.year == 2026 and date.month == 11, 'Check the official holiday calendar for another month'
    return date.weekday() < 5 and (1 <= date.hour < 4 or 6 <= date.hour < 10)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runs', nargs='*', default=[])
    parser.add_argument('--proposals', nargs='+', required=True)
    args = parser.parse_args()
    price = load(HERE / 'pricing.json')
    rates = price['deepseek']
    entries = []
    for name in args.proposals:
        row = load(HERE / 'decisions' / f'{name}-proposal.json')
        low, high = (charge(row['usage'], rates[key]) for key in ('off_peak', 'peak'))
        # Aggregate proposer usage lacks request billing timestamps: retain price bounds.
        entries.append({'name': name, 'kind': 'proposal', 'requests': row['proposer_requests'],
                        'gpt_equivalent': row['proposer_cost'], 'deepseek_bounds': [low, high],
                        'deepseek_estimate': None, 'time_basis': 'aggregate usage; off-peak/peak bounds'})
    for name in args.runs:
        rows = run_rows(name)
        low = high = scheduled = 0.0
        unknown = []
        for task, row in rows.items():
            known = row['requests'] - row['missing_usage']
            factor = row['requests'] / known if known > 0 else 1
            low += charge(row, rates['off_peak']) * factor
            high += charge(row, rates['peak']) * factor
            usage_count = 0
            for path in (PRIVATE / name / 'runs' / ('aster-' + task) / 'provider').glob('*.json'):
                if path.name.endswith('.request.json'):
                    continue
                record = load(path)
                if not record.get('usage'):
                    continue
                usage_count += 1
                usage = record['usage']
                normalized = {**usage, 'cached_tokens': usage.get('input_tokens_details', {}).get('cached_tokens', 0)}
                if record.get('started'):
                    scheduled += charge(normalized, rates['peak' if peak(record['started']) else 'off_peak']) * factor
                else:
                    unknown.append(task)
            if usage_count != known or known <= 0:
                unknown.append(task)
        entries.append({'name': name, 'kind': 'evaluation', 'tasks': len(rows),
                        'requests': sum(r['requests'] for r in rows.values()),
                        'missing_usage': sum(r['missing_usage'] for r in rows.values()),
                        'gpt_equivalent': sum(r['cost'] for r in rows.values()),
                        'deepseek_bounds': [low, high],
                        'deepseek_estimate': None if unknown else scheduled,
                        'unknown_cost_tasks': sorted(set(unknown)),
                        'time_basis': 'per-request UTC start; missing usage imputed at the task mean'})
    total = {'gpt_equivalent': sum(e['gpt_equivalent'] for e in entries),
             'requests': sum(e['requests'] or 0 for e in entries),
             'deepseek_bounds': [sum(e['deepseek_bounds'][i] for e in entries) for i in (0, 1)],
             'deepseek_scheduled_plus_proposer_bounds': [
                 sum(e['deepseek_estimate'] if e['deepseek_estimate'] is not None else e['deepseek_bounds'][i]
                     for e in entries) for i in (0, 1)]}
    result = {'pricing_source': price['source'], 'queried_date': price['queried_date'],
              'not_actual_bills': True, 'includes_excluded_tasks': True, 'entries': entries, 'total': total}
    save(HERE / 'decisions/costs.json', result)
    import json
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
