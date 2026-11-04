"""Offline regression checks for pairing, exclusion and both transcript protocols."""
import json
from pathlib import Path
import tempfile
from unittest.mock import patch

import analyze
import generation
import traces
from common import dev_tasks, run_rows
import propose


def row(requests, passed=True, **extra):
    return dict(requests=requests, cost=requests * .01, passed=passed,
                normal_delivery_and_passed=passed, unreliable=False, missing_usage=0,
                request_errors=0, stop_reason='verified', kind='repository', **extra)


def main():
    seed = {str(i): row(10, i != 0) for i in range(4)}
    candidate = {str(i): row(5) for i in range(4)}
    other = {str(i): row(20) for i in range(4)}
    other['0']['unreliable'] = True
    other['0']['stop_reason'] = 'provider_error'
    tables = {'seed': seed, 'candidate': candidate, 'other': other,
              'evo2-seed-a': candidate, 'evo2-seed-b': seed}
    def fixture_load(path):
        return {'tasks': list(seed)} if Path(path).name == 'manifest.json' else {'stop': False, 'threshold': .8}
    with patch.object(analyze, 'run_rows', side_effect=tables.__getitem__), \
         patch.object(analyze, 'load', side_effect=fixture_load), \
         patch.object(analyze, 'save'):
        result = analyze.compare('paired', ['candidate'], ['seed'])
        assert result['tasks'] == 4 and result['accepted']
        assert result['requests']['ratio'] == .5 and result['requests']['ci90'] == [.5, .5]
        assert result['passed']['difference'] == 1
        # An unrelated arm must not remove a task from this candidate/seed pair.
        damaged = analyze.compare('other', ['other'], ['seed'])
        assert damaged['tasks'] == 3 and damaged['dropped'] == ['0']
        assert result['tasks'] == 4
        final = analyze.compare('final', ['candidate', 'candidate'], ['seed', 'seed'], heldout=True)
        assert 'accepted' not in final and final['repository_requests']['ratio'] == .5
        assert final['passed']['ci90'][0] <= 1 <= final['passed']['ci90'][1]
        noise = analyze.null(['evo2-seed-a', 'evo2-seed-b'])
        assert noise['threshold'] == .5 and noise['stop']
        try:
            analyze.null(['evo2-seed-b', 'evo2-seed-a'])
        except AssertionError:
            pass
        else:
            raise AssertionError('Reversed noise orientation was accepted')
        seed['0']['cost'] = 0
        zero_cost = analyze.compare('zero-cost', ['candidate'], ['seed'])
        assert zero_cost['cost']['ci90'] is None and zero_cost['requests']['ratio'] == .5
        for i in (1, 2):
            other[str(i)].update(unreliable=True, request_errors=3)
        assert analyze.compare('too-damaged', ['other'], ['seed'])['stop']
        assert not analyze.compare('too-damaged', ['other'], ['seed'])['accepted']
        # Missing from every arm still counts against the manifest's full task set.
        with patch.object(analyze, 'load', return_value={'tasks': [str(i) for i in range(20)]}):
            missing = analyze.null(['evo2-seed-a', 'evo2-seed-b'])
            assert missing['stop'] and len(missing['dropped']) == 16
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        job = root / 'seed/runs/aster-task'
        job.mkdir(parents=True)
        (job / 'launch.json').write_text('{}')
        (job / 'result.json').write_text('{}')
        with patch.object(generation, 'WORK', root), patch.object(generation, 'HERE', root), \
             patch.object(generation, 'PRIVATE', root), patch.object(generation, 'prepare'), \
             patch.object(generation, 'sh', return_value=1) as call:
            try:
                generation.evaluate({'seed': 'seed-ref', 'candidate': 'candidate-ref'})
            except AssertionError:
                pass
            else:
                raise AssertionError('Mixed completed/fresh batch was not rejected')
            assert not call.called, 'No evaluator process may start for a mixed batch'
        with patch.object(generation, 'WORK', root), patch.object(generation, 'HERE', root), \
             patch.object(generation, 'PRIVATE', root), patch.object(generation, 'prepare'), \
             patch.object(generation, 'sh', return_value=1) as call:
            record = root / 'decisions/batch-new-seed.json'
            record.parent.mkdir(exist_ok=True)
            record.write_text(json.dumps({'complete': False}))
            try:
                generation.evaluate({'new-seed': 'seed-ref', 'new-candidate': 'candidate-ref'})
            except AssertionError:
                pass
            else:
                raise AssertionError('Reserved unfinished batch was resumed')
            assert not call.called
            status = [{'run': name, 'complete': True} for name in ('new-seed', 'new-candidate')]
            record.write_text(json.dumps(status))
            assert generation.evaluate({'new-seed': 'seed-ref', 'new-candidate': 'candidate-ref'}) == status
            assert not call.called, 'Complete batch reuse must not launch processes'
    with tempfile.TemporaryDirectory() as temp:
        job = Path(temp)
        transcript_row = {**row(1), 'phase': 'completed', 'seconds': 1, 'timed_out': False}
        chat = {'messages': [
            {'role': 'system', 'content': 'SECRET_SYSTEM'},
            {'role': 'user', 'content': 'inspect'},
            {'role': 'assistant', 'content': None, 'tool_calls': [
                {'id': 'call-1', 'function': {'name': 'read_file', 'arguments': '{"path":"a.py"}'}}]},
            {'role': 'tool', 'tool_call_id': 'call-1', 'content': 'source evidence'},
            {'role': 'assistant', 'content': 'finished'},
        ]}
        with patch.object(traces, 'last_request', return_value=chat):
            rendered = traces.render('task', transcript_row, job)
        assert '-> read_file(' in rendered and '<- read_file: source evidence' in rendered
        assert 'finished' in rendered and 'SECRET_SYSTEM' not in rendered
        responses = {'input': [
            {'type': 'function_call', 'call_id': 'c', 'name': 'search', 'arguments': '{}'},
            {'type': 'function_call_output', 'call_id': 'c', 'output': 'match'},
            {'type': 'message', 'role': 'assistant', 'content': [{'text': 'done'}]},
        ]}
        with patch.object(traces, 'last_request', return_value=responses):
            rendered = traces.render('task', transcript_row, job)
        assert '-> search({})' in rendered and '<- match' in rendered and 'done' in rendered
    allowed = {task['id'] for task in dev_tasks()}
    scores = propose.scores(['fin-old20-ds-v15'])
    seen = [line.split('|')[1].strip() for line in scores.splitlines() if line.startswith('| ')][2:]
    assert len(seen) == 12 and set(seen) <= allowed
    counts = run_rows('fin-old20-ds-v15')
    requests = [counts[t]['requests'] for t in seen]
    assert requests == sorted(requests, reverse=True)
    print('PASS: manifest-based exclusions, fixed noise orientation, batch interruption/reuse guards, zero-cost reporting, paired confidence intervals, chat/Responses names, 12 eligible smoke tasks')


if __name__ == '__main__':
    main()
