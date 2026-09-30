"""Summarize every frozen repetition, without selecting best outcomes."""
from __future__ import annotations

import json
import statistics
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
OUT = REPO / 'artifacts/private/subagent-light-20260930'
ARMS = ('solo', 'serial', 'parallel')


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def main():
    rows = [json.loads(path.read_text()) for path in sorted((OUT / 'runs').glob('*/result.json'))]
    if len({(x['task'], x['arm'], x['repeat']) for x in rows}) != len(rows):
        raise ValueError('duplicate_episode')
    summary = {'expected_episodes': 36, 'recorded_episodes': len(rows), 'complete': len(rows) == 36,
               'experiment': 'explicit_read_only_scenarios_not_official_benchmark', 'arms': {}}
    for arm in ARMS:
        group = [x for x in rows if x['arm'] == arm]
        if not group:
            continue
        usage = Counter()
        for row in group:
            usage.update(row['ledger_usage'])
        summary['arms'][arm] = dict(episodes=len(group), passed=sum(x['passed'] for x in group),
            facts_correct=sum(x['grade']['correct'] for x in group), facts_total=sum(x['grade']['total'] for x in group),
            regressions_found=sum(x['grade']['regressions_found'] for x in group),
            regressions=sum(x['grade']['regressions'] for x in group),
            false_positives=sum(x['grade']['false_positives'] for x in group),
            controls=sum(x['grade']['controls'] for x in group),
            seconds=sum(x['seconds'] for x in group), median_seconds=statistics.median(x['seconds'] for x in group),
            ledger_usage=dict(usage), parent_input_tokens=sum(x['parent_input_tokens'] for x in group),
            median_parent_peak_input_tokens=statistics.median(x['parent_peak_input_tokens'] for x in group),
            parent_read_calls=sum(x['parent_read_calls'] for x in group),
            parent_repeated_child_read_calls=sum(x['parent_repeated_child_read_calls'] for x in group),
            model_calls=sum(x['model_calls'] for x in group), tool_calls=sum(x['tool_calls'] for x in group),
            workers_started=sum(x['workers_started'] for x in group), workers_completed=sum(x['workers_completed'] for x in group),
            http_without_total_usage=sum(x['http_without_total_usage'] for x in group),
            accounting_mismatches=sum(not x['usage_matches'] for x in group),
            runtime_failures=sum(x['error'] is not None for x in group),
            source_or_workspace_changes=sum(not x['source_unchanged'] or not x['workspace_unchanged'] for x in group))
    pairs = []
    for task, repeat in sorted({(x['task'], x['repeat']) for x in rows}):
        matched = {x['arm']: x for x in rows if (x['task'], x['repeat']) == (task, repeat)}
        if set(matched) != set(ARMS):
            continue
        item = dict(task=task, repeat=repeat, passed={a:r['passed'] for a,r in matched.items()})
        for comparison, left, right in [('parallel_vs_solo','parallel','solo'),('parallel_vs_serial','parallel','serial')]:
            a,b=matched[left],matched[right]
            item[comparison]=dict(seconds_ratio=a['seconds']/b['seconds'],
                token_ratio=a['ledger_usage'].get('total_tokens',0)/max(1,b['ledger_usage'].get('total_tokens',0)),
                fact_delta=a['grade']['correct']-b['grade']['correct'],
                outcome='win' if a['passed'] and not b['passed'] else 'loss' if b['passed'] and not a['passed'] else 'tie')
        pairs.append(item)
    summary['paired_comparisons']={key:dict(
        pairs=len(pairs), outcomes=dict(Counter(x[key]['outcome'] for x in pairs)),
        median_seconds_ratio=statistics.median(x[key]['seconds_ratio'] for x in pairs),
        median_token_ratio=statistics.median(x[key]['token_ratio'] for x in pairs))
        for key in ('parallel_vs_solo','parallel_vs_serial')} if pairs else {}
    save(HERE/'results.json',rows)
    save(HERE/'summary.json',summary)
    save(HERE/'pairs.json',pairs)
    lines=['# Lightweight subagent scenario experiment','',
        'Given-probe behavior inference and constrained change review (not open-ended defect discovery); 6 tasks × 3 arms × 2 independent pass@1 samples. '
        'Three upstream snapshots are shared between investigation and review tasks; this is not an official benchmark or a holdout.', '',
        f"Recorded {len(rows)}/36 episodes. Results are {'complete' if summary['complete'] else 'INCOMPLETE'}. No best-of-two selection.",'',
        '| Arm | Complete tasks | Correct facts | Review regressions | False positives | Seconds | Total tokens | Parent input tokens | Children complete/started |',
        '|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    for arm,item in summary['arms'].items():
        lines.append(f"| {arm} | {item['passed']}/{item['episodes']} | {item['facts_correct']}/{item['facts_total']} | "
            f"{item['regressions_found']}/{item['regressions']} | {item['false_positives']}/{item['controls']} | "
            f"{item['seconds']:.1f} | {item['ledger_usage'].get('total_tokens',0):,} | {item['parent_input_tokens']:,} | "
            f"{item['workers_completed']}/{item['workers_started']} |")
    lines+=['','Costs are provider-reported tokens, not dollar charges. Unknown HTTP usage is retained in summary.json. '
            'Parent input tokens are cumulative repeated prompt consumption; median peak input tokens are reported separately.', '',
            'Accuracy uses exact executable behavior, review regression classification, and mechanically valid source citations. '
            'Citation semantics and explanation quality are not automatically judged. A task passes only if all eight facts pass, '
            'the runtime completes normally and the workspace is unchanged.', '',
            'Serial and parallel use the same explicitly assigned two subtasks and integration step. Their comparison measures scheduling; '
            'solo versus delegated arms also changes context separation and coordination. This does not test autonomous task decomposition, '
            'patch implementation, persistent follow-up or recovery UX. Old Worker results remain unchanged.', '']
    (HERE/'REPORT.md').write_text('\n'.join(lines))
    print(json.dumps(summary,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
