"""Replay the production meter against saved receipts without provider access."""

import argparse
import json
import statistics
from pathlib import Path

from metrics import percentile, requests, stable

from aster.context.budget import BudgetCalculator
from aster.providers.usage import normalize_usage
from aster.schema import ModelReply


def replay(runs):
    rows = []
    for directory in sorted(runs.glob('aster-*/provider')):
        calculator = BudgetCalculator()
        previous = None
        for metadata, payload in requests(directory):
            prompt = payload['input']
            estimate, basis = calculator.estimate(prompt, payload)
            usage = metadata.get('usage') or {}
            inputs = usage.get('input_tokens', 0)
            if inputs:
                rows.append(dict(task=directory.parent.name.removeprefix('aster-'),
                                 request_id=metadata['request_id'], basis=basis,
                                 stable=previous is not None and stable(previous, prompt),
                                 estimated=estimate, actual=inputs, ratio=estimate / inputs))
                previous = prompt
            calculator.observe(payload, ModelReply(usage=normalize_usage(usage)), prompt)
    ratios = [r['ratio'] for r in rows if r['basis'] == 'receipt']
    stable_rows = [r for r in rows if r['stable']]
    summary = dict(requests_with_usage=len(rows), receipt_requests=len(ratios),
                   median=statistics.median(ratios), p95=percentile(ratios, .95),
                   minimum=min(ratios), maximum=max(ratios),
                   stable_requests=len(stable_rows),
                   stable_receipts=sum(r['basis'] == 'receipt' for r in stable_rows))
    summary['stable_receipt_fraction'] = summary['stable_receipts'] / len(stable_rows)
    summary['passed'] = (summary['median'] <= 1.02 and summary['p95'] <= 1.05
                         and summary['minimum'] >= .99 and summary['maximum'] <= 1.15
                         and summary['stable_receipt_fraction'] >= .95)
    return {'summary': summary, 'requests': rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('runs', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = replay(args.runs)
    if args.output:
        args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result['summary'], indent=2))
    if not result['summary']['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
