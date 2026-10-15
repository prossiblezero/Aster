"""Runnable checks of historical metrics, acceptance boundaries and raw-data immutability."""
import copy
import io
import json
import tarfile
import tempfile
from pathlib import Path

from audit import declarations, quality, trace_audit
from evaluate import HERE, REPO, STABLE_FAIL, STABLE_PASS, VARIABLE, paths, pending_tasks, selection
from metrics import measure, requests
from turn_metrics import measure as turn_measure
from turn_metrics import measure_items


def rejected(call):
    try:
        call()
    except AssertionError:
        return
    raise AssertionError('Expected rejection')


def main():
    runs = REPO / 'artifacts/private/context-state-20261014/runs'
    turns = turn_measure(runs)
    baseline = json.loads((HERE / 'baseline-turns.json').read_text())
    assert turns == baseline
    assert turns['turns'] == 358 and turns['reread_only_turns'] == 27
    cache = measure(runs)
    old = json.loads((HERE.parent / 'context-state-20261014/metrics.json').read_text())
    for key, value in old['agents']['aster'].items():
        assert cache['agents']['aster'][key] == value, key
    current = cache['agents']['aster']
    assert current['requests_with_usage'] == 378
    assert 3.51 < current['weighted_cost'] < 3.53
    root = runs / 'aster-aider-sgf-parsing'
    trace = trace_audit(root, requests(root / 'provider'))
    assert not trace['unmatched_foreground_requests']
    assert all(c['policy_version'] == 'deterministic-v10' for c in trace['contexts'])

    manifest = {'tasks': STABLE_PASS + STABLE_FAIL + VARIABLE}
    assert len(selection(manifest, 1, None)) == 20
    assert len(selection(manifest, 2, None)) == len(selection(manifest, 3, None)) == 6
    rejected(lambda: selection(manifest, 2, STABLE_FAIL))
    rejected(lambda: selection(manifest, 1, ['unknown']))
    rejected(lambda: selection(manifest, 1, [VARIABLE[0], VARIABLE[0]]))
    assert len({paths(r, a)[1] for r in (1, 2) for a in (1, 2, 3)}) == 6
    rows = [dict(task=t, attempt=1, graded=True, passed=t in STABLE_PASS) for t in manifest['tasks']]
    rows += [dict(task=t, attempt=a, graded=True, passed=False) for t in VARIABLE for a in (2, 3)]
    variable_rows = [r for r in rows if r['task'] in VARIABLE]
    for row in variable_rows[:8]:
        row['passed'] = True
    q = quality(rows)
    assert q['all_planned_scored'] and q['stable_pass'] and q['variable_passes'] == 8
    assert not quality(rows[:-1])['all_planned_scored']
    variable_rows[7]['passed'] = False
    assert quality(rows)['variable_passes'] == 7
    broken = copy.deepcopy(rows)
    broken[0]['passed'] = False
    assert not quality(broken)['stable_pass']
    broken += [dict(task=broken[0]['task'], attempt=a, graded=True, passed=True) for a in (2, 3)]
    assert quality(broken)['stable_pass'] and quality(broken)['allowed_attempts_only']
    broken[-1]['passed'] = False
    assert not quality(broken)['stable_pass']
    rejected(lambda: quality(rows + [rows[0]]))
    assert not quality(rows + [dict(task=STABLE_FAIL[0], attempt=2, graded=True, passed=True)])['allowed_attempts_only']

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / 'tasks.json').write_text(json.dumps([{'id': VARIABLE[0]}]))
        assert len(pending_tasks(root, [VARIABLE[0]])) == 1
        raw = root / 'runs' / ('aster-' + VARIABLE[0])
        raw.mkdir(parents=True)
        launch = raw / 'launch.json'
        launch.write_text('{"immutable":true}')
        before = launch.read_bytes()
        rejected(lambda: pending_tasks(root, [VARIABLE[0]]))
        (raw / 'result.json').write_text('{}')
        assert pending_tasks(root, [VARIABLE[0]]) == [] and launch.read_bytes() == before
        events = [dict(event='tool_result', seq=1, call={'name': 'run_tests'}, outcome={
            'evidence_id': 'baseline-proof', 'data': {'baseline_results': {'test-id': 'failed'},
            'baseline_check': {'test_results': {'test-id': 'failed'}, 'command': 'pytest -q'}}}),
            dict(event='tool_result', seq=2, call={'name': 'run_tests'}, outcome={
            'data': {'accepted_preexisting': ['test-id'], 'command': 'pytest -q'}})]
        data = '\n'.join(json.dumps(e) for e in events).encode()
        with tarfile.open(root / '.aster.tar', 'w') as bundle:
            item = tarfile.TarInfo('.aster/runs/example/trace.jsonl')
            item.size = len(data)
            bundle.addfile(item, io.BytesIO(data))
        accepted = declarations(root)
        assert accepted[0]['baseline']['evidence_id'] == 'baseline-proof'
        assert accepted[0]['baseline']['status'] == 'failed'
    items = [dict(type='function_call', call_id='c', name='run_tests', arguments='{}'),
             dict(type='function_call_output', call_id='c', output=json.dumps({
                 'content': 'Accepted as pre-existing: ["a", "b"]. Not accepted (no baseline failure on record): []'}))]
    assert measure_items(items)['preexisting_declared'] == 2
    report = {'v10_metrics_reproduced': True, 'v10_turns_reproduced': True,
              'immutable_attempts_checked': True, 'quality_boundary_checks': True,
              'declaration_evidence_checks': True, 'trace_association_checked': True,
              'foreground_with_usage': current['requests_with_usage'],
              'weighted_cost_v10': current['weighted_cost'], 'model_requests_made': 0}
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
