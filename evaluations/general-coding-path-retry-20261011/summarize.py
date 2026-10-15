"""Export the single attempt and an explicitly selective twenty-task comparison."""
import gzip
import hashlib
import json
import re
import tarfile

import evaluate as runner

HERE, PRIVATE = runner.HERE, runner.PRIVATE
load, save = runner.load, runner.save
common = runner.imported('path_retry_summary', runner.baseline.HERE / 'summarize.py')


def main():
    frozen = runner.check_sources()
    root = PRIVATE / 'runs' / ('aster-' + runner.TASK)
    launch = load(root / 'launch.json')
    prior_launch = load(runner.PREVIOUS / 'runs' / root.name / 'launch.json')
    for key in ('prompt_sha256', 'image_id', 'model', 'seconds_limit'):
        assert launch[key] == prior_launch[key], key
    assert launch['prompt_sha256'] == hashlib.sha256(launch['prompt'].encode()).hexdigest()
    task = load(PRIVATE / 'tasks.json')[0]
    tests = PRIVATE / 'sources/terminal-tasks' / task['task_name'] / 'tests'
    test_hashes = runner.hashes(tests)
    old_manifest = load(HERE.parent / 'general-coding-optimized-v2-20261011/manifest.json')
    for name, digest in test_hashes.items():
        assert digest == old_manifest['terminal_source_hashes'][task['task_name'] + '/tests/' + name]
    row = load(root / 'result.json')
    counts, requests = common.usage(root)
    for request in requests:
        sent = load(root / 'provider' / (request['request_id'] + '.request.json'))
        assert sent['model'] == frozen['model']
        assert sent['reasoning']['effort'] == 'medium' and sent['max_output_tokens'] == 8192
        assert request.get('response_model') in (None, frozen['model'])
    row.update(counts)
    row['final'], row['phase'], row['stop_reason'] = common.final_text(root, 'aster')
    row.update(graded=True, passed=row['grade']['passed'], grading_error=row['grade']['tests'] == 0)
    state = load(root / 'aster-result.json') if (root / 'aster-result.json').exists() else {}
    row['runtime'] = {key: state.get(key) for key in (
        'turn', 'step', 'verification', 'verification_failures', 'final_revisions')}
    log = (root / 'grading/test_output.txt').read_text()
    match = re.search(r'Summary: (\d+) passed, (\d+) failed out of (\d+) tests', log)
    if match:
        row['program_comparisons'] = dict(zip(
            ('passed', 'failed', 'total'), map(int, match.groups()), strict=True))
    target = HERE / 'results' / runner.TASK
    save(target / 'launch.json', launch)
    save(target / 'result.json', row)
    save(target / 'usage.json', {'counts': counts, 'requests': requests})
    calls, outcomes = {}, {}
    for request in requests:
        payload = load(root / 'provider' / (request['request_id'] + '.request.json'))
        for event in payload.get('input', []):
            if event.get('type') == 'function_call' and event.get('name') == 'run_shell':
                calls[event['call_id']] = json.loads(event['arguments'])
            elif event.get('type') == 'function_call_output':
                outcomes[event['call_id']] = json.loads(event['output'])
    save(target / 'path-evidence.json', [
        {'call_id': key, 'arguments': arguments, 'outcome': outcomes.get(key)}
        for key, arguments in calls.items() if '/app' in arguments.get('command', '')
    ])
    (target / 'tests.txt.gz').write_bytes(gzip.compress(log.encode(), mtime=0))
    for name in ('junit.xml', 'grade.json'):
        source = root / 'grading' / name
        if source.exists():
            (target / name).write_bytes(source.read_bytes())
    candidate_hashes = {}
    with tarfile.open(root / 'candidate.tar') as archive:
        for member in archive.getmembers():
            if not member.isfile() or member.name.startswith('app/.aster/'):
                continue
            data = archive.extractfile(member).read()
            candidate_hashes[member.name] = hashlib.sha256(data).hexdigest()
            if member.name == 'app/eval.scm':
                (target / 'eval.scm').write_bytes(data)
    save(target / 'candidate-hashes.json', candidate_hashes)
    save(HERE / 'results.json', [row])
    prior = load(HERE.parent / 'general-coding-optimized-v2-20261011/results.json')
    combined = [row if r['task'] == runner.TASK else r for r in prior]
    assert len(combined) == 20 and sum(r['task'] == runner.TASK for r in combined) == 1
    summary = {
        'retry_task': runner.TASK, 'attempts': 1, 'retry_passed': row['passed'],
        'phase': row['phase'], 'stop_reason': row['stop_reason'], 'seconds': row['seconds'],
        'prior_full_run_passed': sum(r['passed'] for r in prior),
        'selective_combined_passed': sum(r['passed'] for r in combined), 'total_tasks': 20,
        'pi_frozen_passed': 15, 'codex_frozen_passed': 14,
        'normal_delivery_and_passed': sum(bool(r.get('final')) and r['exit_code'] == 0 and r['passed'] for r in combined),
        'score_policy': frozen['score_policy'], **counts,
    }
    save(HERE / 'summary.json', summary)
    save(HERE / 'combined/results.json', combined)
    save(HERE / 'audit.json', {
        'one_fresh_attempt': True, 'same_prompt_image_model_budget': True,
        'frozen_runtime_and_inputs_match': True, 'unchanged_upstream_test_hashes': test_hashes,
        'original_interpreter_unchanged': candidate_hashes.get('app/interp.py') ==
            old_manifest['terminal_source_hashes'][task['task_name'] + '/environment/tests/interp.py'],
        'remaining_nineteen_results_unchanged': True,
        'candidate_tar_sha256': hashlib.sha256((root / 'candidate.tar').read_bytes()).hexdigest(),
        'summarizer_sha256': hashlib.sha256(open(__file__, 'rb').read()).hexdigest(),
    })
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
