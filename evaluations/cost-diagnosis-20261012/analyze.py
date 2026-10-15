"""Read preserved traces to diagnose latency and request differences; no model calls."""
import heapq
import importlib.util
import json
import tarfile
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
PRIVATE = REPO / 'artifacts/private'
CURRENT = PRIVATE / 'general-coding-cost-20261011/full-candidate/runs'
ORIGINAL = PRIVATE / 'general-coding-20261010/runs'
spec = importlib.util.spec_from_file_location(
    'preserved_usage', ROOT.parent / 'general-coding-20261010/summarize.py')
usage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(usage)


def trace(run):
    before, after, batches = [], [], []
    context = provider = None
    tools, failures = Counter(), Counter()
    duplicate_tests, seen_tests = 0, set()
    with tarfile.open(run / '.aster.tar') as archive:
        members = archive.getmembers()
        member = next(m for m in members if m.name.endswith('/trace.jsonl'))
        for line in archive.extractfile(member):
            event = json.loads(line)
            now = datetime.fromisoformat(event['time']).timestamp()
            name = event['event']
            if name == 'context_built':
                context = now
            elif name == 'model_stream' and event.get('type') == 'begin' and context is not None:
                before.append(now - context)
                context = None
            elif name == 'provider_attempt':
                provider = now
            elif name == 'model_reply' and provider is not None:
                after.append(now - provider)
                provider = None
            elif name == 'tool_batch_finished':
                batches.append(event['duration_ms'] / 1000)
            elif name == 'tool_result':
                call, outcome = event['call'], event['outcome']
                tools[call['name']] += 1
                if outcome['status'] != 'success':
                    failures['/'.join((call['name'], outcome['status'], outcome.get('reason', '')))] += 1
                if call['name'] == 'run_tests' and outcome['status'] == 'success':
                    fingerprint = outcome.get('data', {}).get('tested_workspace_hash')
                    key = (json.dumps(call['arguments'], sort_keys=True), fingerprint)
                    if fingerprint:
                        duplicate_tests += key in seen_tests
                        seen_tests.add(key)
        checkpoint = next(m for m in members if m.name.endswith('/checkpoint.json'))
        data = json.loads(archive.extractfile(checkpoint).read())
        fields = {k: len(json.dumps(v).encode()) for k, v in data.items()}
    return {
        'task': run.name.removeprefix('aster-'),
        'before_request_gap_seconds': round(sum(before), 3),
        'after_reply_gap_seconds': round(sum(after), 3),
        'tool_batch_seconds': round(sum(batches), 3),
        'checkpoint_bytes': checkpoint.size,
        'largest_serialized_fields': sorted(fields.items(), key=lambda v: v[1], reverse=True)[:3],
        'tools': dict(tools), 'unsuccessful_tools': dict(failures),
        'repeated_successful_test_same_arguments_and_workspace': duplicate_tests,
    }


def requests(root, agent):
    counts = Counter()
    for run in sorted(root.glob(agent + '-*')):
        _, rows = usage.usage(run)
        for row in rows:
            payload = usage.load(run / 'provider' / (row['request_id'] + '.request.json'))
            counts['requests'] += 1
            counts['with_prompt_cache_key'] += bool(payload.get('prompt_cache_key'))
    return dict(counts)


def main():
    rows = [trace(run) for run in sorted(CURRENT.glob('aster-*'))]
    summary = {key: round(sum(r[key] for r in rows), 3) for key in (
        'before_request_gap_seconds', 'after_reply_gap_seconds', 'tool_batch_seconds',
        'repeated_successful_test_same_arguments_and_workspace')}
    launches = [usage.load(p / 'launch.json') for p in CURRENT.glob('aster-*')]
    summary['wall_seconds_from_launch_to_last_result_file'] = round(
        max((p / 'result.json').stat().st_mtime for p in CURRENT.glob('aster-*'))
        - min(r['started'] for r in launches), 3)
    results = usage.load(ROOT.parent / 'general-coding-cost-20261011/full-candidate/results.json')
    scheduling = {}
    for concurrency in (2, 4):
        slots = [0.0] * concurrency
        for result in results:
            heapq.heapreplace(slots, slots[0] + result['seconds'])
        scheduling[concurrency] = round(max(slots), 3)
    output = {
        'scope': 'Offline diagnosis, no model calls or score changes.',
        'gap_limit': 'Gaps include checkpoint work but are not checkpoint-exclusive measurements.',
        'scheduling_limit': 'Simulation fixes observed episode durations and omits setup and scoring; higher concurrency may change provider latency.',
        'summary': summary, 'tasks': rows, 'scheduling_simulation_seconds': scheduling,
        'request_fields': {'aster': requests(CURRENT, 'aster'),
                           'pi': requests(ORIGINAL, 'pi'), 'codex': requests(ORIGINAL, 'codex')},
    }
    usage.save(ROOT / 'summary.json', output)
    print(json.dumps({k: v for k, v in output.items() if k != 'tasks'}, indent=2))


if __name__ == '__main__':
    main()
