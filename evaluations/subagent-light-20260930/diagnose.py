"""Post-hoc diagnostics; preserve the frozen primary score and every recorded episode."""
import hashlib
import json
import statistics
import subprocess
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT = REPO/'artifacts/private/subagent-light-20260930'
HERE = Path(__file__).resolve().parent
ARMS = ('solo','serial','parallel')


def median(values):
    return statistics.median(values) if values else None


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def json_answer(text):
    text=text.strip()
    if text.startswith('```') and text.endswith('```'):
        text=text.split('\n',1)[1].rsplit('```',1)[0]
    try:
        rows=json.loads(text)['answers']
        return rows if isinstance(rows,list) else []
    except (ValueError,KeyError,TypeError):
        return []


def main():
    frozen=json.loads((OUT/'freeze.json').read_text())
    data=json.loads((REPO/'datasets/subagent-light-20260930/instances.json').read_text())
    expected={(t['id'],a,r) for t in data for a in ARMS for r in (1,2)}
    source_checks={name:hashlib.sha256(subprocess.check_output(
        ['git','show',f"{frozen['commit']}:src/aster/{name}"],cwd=REPO)).hexdigest()==digest
        for name,digest in frozen['source'].items()}
    file_checks={name:sha(REPO/name)==digest for name,digest in frozen['files'].items()}
    episodes=[]
    for path in sorted((OUT/'runs').glob('*/result.json')):
        raw=json.loads(path.read_text())
        ledger=json.loads((path.parent/'request-ledger.json').read_text())
        checks=raw['grade'].get('checks',[])
        ids={q['id'] for t in data if t['id']==raw['task'] for g in t['groups'] for q in g['questions']}
        answer=json_answer(raw['final'])
        answer_ids=[r.get('id') for r in answer if isinstance(r,dict)]
        structure=len(answer_ids)==len(answer)==len(ids) and set(answer_ids)==ids
        behavior=sum(c['observed'] and c['regression'] for c in checks)
        attempts=[a for r in ledger for a in r['attempts']]
        non_success=[a for a in attempts if a.get('classification')!='success']
        known_attempts=[a for a in attempts if 'total_tokens' in a.get('usage',{})]
        children=[r for r in raw['runs'] if r.get('parent_run_id')]
        parents=[r for r in raw['runs'] if not r.get('parent_run_id')]
        parent_trace=OUT/'workspaces'/path.parent.name/'.aster/runs'/parents[0]['run_id']/'trace.jsonl'
        parent_events=[json.loads(line) for line in parent_trace.read_text().splitlines()]
        handoffs=sum(e['event']=='worker_handoff_delivered' for e in parent_events)
        assignment_ids=[r['worker_id'] for r in raw['explicit_assignments']]
        row=dict(task=raw['task'],arm=raw['arm'],repeat=raw['repeat'],
            strict_passed=raw['passed'],strict_facts=raw['grade']['correct'],total_facts=raw['grade']['total'],
            observed_only_facts=sum(c['observed'] for c in checks),behavior_and_verdict_facts=behavior,
            behavior_diagnostic_passed=structure and behavior==len(ids) and raw['phase']=='completed'
                and raw['error'] is None and raw['source_unchanged'] and raw['workspace_unchanged'],
            citation_only_failures=[c['id'] for c in checks if c['observed'] and c['regression'] and not c['evidence']],
            observed_errors=[c['id'] for c in checks if not c['observed']],
            verdict_errors=[c['id'] for c in checks if not c['regression']],parse_error=raw['grade']['parse_error'],
            seconds=raw['seconds'],known_reported_tokens=raw['ledger_usage'].get('total_tokens',0),
            http_attempts=len(attempts),unknown_usage_attempts=len(attempts)-len(known_attempts),
            retry_requests=sum(len(r['attempts'])>1 for r in ledger),
            extra_attempts=sum(max(0,len(r['attempts'])-1) for r in ledger),
            non_success_attempts=dict(Counter(a.get('classification','missing') for a in non_success)),
            non_success_attempt_elapsed_sum=sum(a.get('elapsed_seconds',0) for a in non_success),
            retry_free=not non_success and all(len(r['attempts'])==1 for r in ledger),
            slow_requests=[dict(role=r.get('role'),seconds=r['seconds'],attempts=len(r['attempts']),
                non_success_attempts=sum(a.get('classification')!='success' for a in r['attempts']))
                for r in ledger if r['seconds']>=90],
            parent_input_tokens=raw['parent_input_tokens'],parent_peak_input_tokens=raw['parent_peak_input_tokens'],
            parent_handoffs=handoffs,
            parent_read_calls=raw['parent_read_calls'],parent_repeated_child_read_calls=raw['parent_repeated_child_read_calls'],
            audit=dict(source_unchanged=raw['source_unchanged'],workspace_unchanged=raw['workspace_unchanged'],
                usage_matches=raw['usage_matches'],expected_key=(raw['task'],raw['arm'],raw['repeat']) in expected,
                expected_child_count=len(children)==(0 if raw['arm']=='solo' else 2),
                child_runs_match_parent=len(parents)==1 and all(r['parent_run_id']==parents[0]['run_id'] for r in children),
                assignments_count=len(assignment_ids)==(0 if raw['arm']=='solo' else 2),
                handoffs_reached_parent=handoffs==(0 if raw['arm']=='solo' else 2),
                local_allocations_equal=all(r['local_allocation']=={'max_steps':24,'max_turns':14} for r in raw['explicit_assignments']),
                attempt_known_tokens_match_ledger=sum(a['usage']['total_tokens'] for a in known_attempts)==raw['ledger_usage'].get('total_tokens',0)),
            source_result=str(path.relative_to(REPO)))
        episodes.append(row)
    summary={a:dict(episodes=len(rows),strict_tasks=sum(r['strict_passed'] for r in rows),
        strict_facts=sum(r['strict_facts'] for r in rows),facts=sum(r['total_facts'] for r in rows),
        posthoc_behavior_tasks=sum(r['behavior_diagnostic_passed'] for r in rows),
        posthoc_behavior_facts=sum(r['behavior_and_verdict_facts'] for r in rows),
        citation_only_errors=sum(len(r['citation_only_failures']) for r in rows),
        actual_output_errors=sum(len(r['observed_errors']) for r in rows),
        verdict_errors=sum(len(r['verdict_errors']) for r in rows),
        parse_error_episodes=sum(r['parse_error'] for r in rows),
        unscorable_facts=sum(r['total_facts'] for r in rows if r['parse_error']),
        wall_seconds=sum(r['seconds'] for r in rows),known_reported_tokens=sum(r['known_reported_tokens'] for r in rows),
        unknown_usage_attempts=sum(r['unknown_usage_attempts'] for r in rows),
        episodes_with_retries=sum(not r['retry_free'] for r in rows),
        retry_attempt_elapsed_sum=sum(r['non_success_attempt_elapsed_sum'] for r in rows))
        for a in ARMS if (rows:=[r for r in episodes if r['arm']==a])}
    pairsets={}
    for comparator in ('solo','serial'):
        pairs=[]
        for task,repeat in sorted({(r['task'],r['repeat']) for r in episodes}):
            byarm={r['arm']:r for r in episodes if (r['task'],r['repeat'])==(task,repeat)}
            if 'parallel' not in byarm or comparator not in byarm:
                continue
            left,right=byarm['parallel'],byarm[comparator]
            pairs.append(dict(task=task,repeat=repeat,
                parallel_over_comparator_seconds=left['seconds']/right['seconds'],
                parallel_over_comparator_tokens=left['known_reported_tokens']/max(1,right['known_reported_tokens']),
                strict_both_pass=left['strict_passed'] and right['strict_passed'],
                behavior_both_pass=left['behavior_diagnostic_passed'] and right['behavior_diagnostic_passed'],
                retry_free=left['retry_free'] and right['retry_free'],
                token_usage_complete=left['unknown_usage_attempts']==right['unknown_usage_attempts']==0))
        selectors={'all_matched':lambda p:True,'strict_both_pass':lambda p:p['strict_both_pass'],
            'posthoc_behavior_both_pass':lambda p:p['behavior_both_pass'],
            'posthoc_retry_free':lambda p:p['retry_free'],
            'posthoc_behavior_both_pass_retry_free':lambda p:p['behavior_both_pass'] and p['retry_free']}
        pairsets['parallel_vs_'+comparator]=dict(pairs=pairs,
            subsets={name:dict(n=len(selected),expected_matched_pairs=12,
                median_wall_ratio=median([p['parallel_over_comparator_seconds'] for p in selected]),
                median_known_token_ratio=median([p['parallel_over_comparator_tokens'] for p in selected]),
                incomplete_usage_pairs=sum(not p['token_usage_complete'] for p in selected))
                for name,predicate in selectors.items() if (selected:=[p for p in pairs if predicate(p)]) or True})
    result=dict(posthoc=True,replaces_primary=False,completed=len(episodes),expected=36,
        frozen_commit=frozen['commit'],import_path=frozen['import_path'],python=frozen['python'],
        source_hash_checks=source_checks,frozen_file_checks=file_checks,
        audit_failures=[dict(task=r['task'],arm=r['arm'],repeat=r['repeat'],failures=[k for k,v in r['audit'].items() if not v])
            for r in episodes if not all(r['audit'].values())],
        summary=summary,pairsets=pairsets,episodes=episodes)
    (HERE/'diagnostics.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(dict(completed=len(episodes),summary=summary,audit_failures=result['audit_failures']),ensure_ascii=False,indent=2))

if __name__=='__main__':
    main()
