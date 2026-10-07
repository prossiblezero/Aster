"""Fixed-delegation and identical-read-prefix r5/r6 experiment; stdlib harness only."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
OUT = REPO / 'artifacts/private' / HERE.name
HISTORY = REPO / 'artifacts/private/subagent-ten-20261003'
spec = importlib.util.spec_from_file_location('ten_controlled', HERE.parent / 'subagent-ten-20261003/evaluate.py')
ten = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ten)
save, digest = ten.save, ten.digest
VERSIONS = ('r5', 'r6')
HISTORICAL = ('boltons-investigate--team--1', 'more-itertools-investigate--team--1',
              'more-itertools-investigate--team--2')


def read(path):
    return json.loads(path.read_text())


def trace(path):
    previous = ''
    events = []
    for seq, line in enumerate(path.read_text().splitlines(), 1):
        event = json.loads(line)
        actual = event.pop('hash')
        assert event['seq'] == seq and event['previous'] == previous
        assert hashlib.sha256(json.dumps(event, sort_keys=True, ensure_ascii=False).encode()).hexdigest() == actual
        previous = actual
        events.append(event)
    return events


def prefixes():
    records = []
    # All actual delegations in the adopted r5 round, selected before new outcomes.
    actual = sorted(p.parent.name for p in (HISTORY / 'r5/runs').glob('*/result.json')
                    if read(p)['workers_started'])
    assert actual == sorted(HISTORICAL)
    for name in HISTORICAL:
        result_path = HISTORY / 'r5/runs' / name / 'result.json'
        record = dict(id=name, task=read(result_path)['task'], parent=[], child=[],
                      provenance={str(result_path.relative_to(REPO)): digest(result_path)})
        runs = read(result_path)['runs']
        assert len(runs) == 2
        for run in runs:
            path = HISTORY / 'r5/workspaces' / name / '.aster/runs' / run['run_id'] / 'trace.jsonl'
            record['provenance'][str(path.relative_to(REPO))] = digest(path)
            role = 'child' if run['parent_run_id'] else 'parent'
            for event in trace(path):
                if role == 'parent' and event['event'] == 'worker_handoff_delivered':
                    break
                if event['event'] != 'tool_result':
                    continue
                call, outcome = event['call'], event['outcome']
                data = outcome.get('data', {})
                if role == 'parent' and call['name'] == 'worker_status' and any(
                        item.get('delivery') for item in data.get('workers', [data])):
                    break
                if call['name'] == 'read_file' and outcome['status'] == 'success' and data['path'] != 'REQUEST.md':
                    record[role].append({key: data[key] for key in ('path', 'start', 'end', 'source_sha256')})
        assert record['parent'] and record['child']
        records.append(record)
    save(HERE / 'PREFIXES.json', records)
    return records


class NoModel:
    async def complete(self, *args, **kwargs):
        raise AssertionError('preflight_must_not_call_model')


async def setup(base, config, task, root, limit, prefix=None, ledger=None, probe=False):
    from aster.schema import ToolCall
    prompt = ten.prepare(base, task, root, 'parent_child')
    runtime = base.Runtime(root, config, model=NoModel() if probe else None,
                           hooks=base.Hooks([ledger]) if ledger else None)
    runtime.start(base.TaskSpec(prompt=prompt, max_steps=60, max_turns=36, max_tokens=limit,
                               require_observation=True, require_edit=False, require_verification=False))
    group, own = task['groups']
    assignment = ('Complete only work package ' + group['name'] + '. Questions: '
                  + ', '.join(q['id'] for q in group['questions'])
                  + '. Return all assigned answer rows with source evidence using the original JSON contract. '
                  'Do not answer the other package.')
    result = await runtime.workers.start(assignment, mode='explore', explicit=True, parent_work=(
        'Independently solve package ' + own['name'] + ': ' + ', '.join(q['id'] for q in own['questions'])
        + ". Then integrate the child's other package into the complete JSON response."))
    assert result['status'] == 'running', result
    worker = runtime.workers.workers[result['worker_id']]
    # Evaluation only: cancel before the newly created task gets its first event-loop turn.
    worker.future.cancel()
    await asyncio.gather(worker.future, return_exceptions=True)
    assert worker.runtime.state.turn == 0 and not worker.runtime.state.terminal
    worker.future = asyncio.get_running_loop().create_future()
    if prefix:
        for role, target in (('parent', runtime), ('child', worker.runtime)):
            for item in prefix[role]:
                result = await target.step(ToolCall(name='read_file', arguments={
                    key: item[key] for key in ('path', 'start', 'end')}))
                assert result.status == 'success', result.reason
                assert all(result.data[key] == item[key] for key in item), 'prefix_evidence_changed'
    runtime.prepare_request()
    definitions = None
    if probe:
        _, definitions, _ = worker.runtime.prepare_request()
    start = dict(parent_estimate=runtime._request_estimate, child_estimate=worker.runtime._request_estimate,
                 reserve=runtime.workers.budget.token_reserve(),
                 child_tools=len(definitions) if definitions is not None else None,
                 handoff_reason=worker.runtime._handoff_reason, limit=limit,
                 parent_reads=len(prefix['parent']) if prefix else 0,
                 child_reads=len(prefix['child']) if prefix else 0,
                 model_calls=runtime.state.team_usage.get('model_calls', 0))
    assert start['model_calls'] == 0 and worker.runtime.state.turn == 0
    return runtime, worker, start


async def cleanup(runtime, worker):
    if not worker.future.done():
        worker.future.cancel()
    await asyncio.gather(worker.future, return_exceptions=True)
    await runtime.workers.close()
    await worker.runtime.workers.close()
    worker.runtime.sandbox.close()
    runtime.sandbox.close()


async def probe(version):
    base = ten.shared.base(HISTORY / version / 'src')
    config = base.settings('parallel')
    tasks = ten.cases.bank(base)
    records = []
    source_prefixes = read(HERE / 'PREFIXES.json')
    if version == 'r5':
        save(OUT / 'oracle.json', ten.preflight(base, 'parent_child'))
    for prefix in source_prefixes:
        task = next(t for t in tasks if t['id'] == prefix['task'])
        with tempfile.TemporaryDirectory(prefix='aster-controlled-roomy-') as folder:
            runtime, worker, roomy = await setup(base, config, task, Path(folder) / 'workspace', 300000, prefix, probe=True)
            assert roomy['child_tools'] and not roomy['handoff_reason']
            assert runtime.workers.budget.admit(worker.runtime, 'model_calls', worker.runtime._request_estimate) == ''
            await cleanup(runtime, worker)
        limit = (2 * roomy['parent_estimate'] + roomy['child_estimate'] * 5 // 4 if version == 'r5'
                 else next(r['limit'] for r in read(OUT / 'probe-r5.json')['prefixes'] if r['id'] == prefix['id']))
        with tempfile.TemporaryDirectory(prefix='aster-controlled-tight-') as folder:
            runtime, worker, tight = await setup(base, config, task, Path(folder) / 'workspace', limit, prefix, probe=True)
            if version == 'r5':
                assert tight['child_tools'] and not tight['handoff_reason']
                assert tight['child_estimate'] + tight['reserve'] <= limit < 2 * tight['child_estimate'] + tight['reserve']
            else:
                assert not tight['child_tools'] and tight['handoff_reason'] == 'team_token_limit'
            assert runtime.workers.budget.admit(worker.runtime, 'model_calls', worker.runtime._request_estimate) == ''
            await cleanup(runtime, worker)
        records.append(dict(id=prefix['id'], limit=limit, roomy=roomy, pressure=tight))
    # Every task must really start a bounded worker at the normal budget too.
    for task in tasks:
        with tempfile.TemporaryDirectory(prefix='aster-controlled-start-') as folder:
            runtime, worker, start = await setup(base, config, task, Path(folder) / 'workspace', 300000, probe=True)
            assert start['child_tools'] and not start['handoff_reason']
            await cleanup(runtime, worker)
    save(OUT / ('probe-' + version + '.json'), dict(prefixes=records, tasks_checked=len(tasks),
         settings=config.identity(), endpoint_sha256=hashlib.sha256(config.base_url.encode()).hexdigest()))
    print(version + ': zero-model preflight passed (10 starts, 3 pressure, 3 roomy)', flush=True)


def freeze():
    assert not (OUT / 'freeze.json').exists() and not (OUT / 'runs').exists()
    source_prefixes = prefixes()
    for version in VERSIONS:
        subprocess.run([sys.executable, str(__file__), 'probe', '--version', version], check=True)
    probes = {v: read(OUT / ('probe-' + v + '.json')) for v in VERSIONS}
    assert probes['r5']['settings'] == probes['r6']['settings']
    assert probes['r5']['endpoint_sha256'] == probes['r6']['endpoint_sha256']
    tasks = read(ten.DATA / 'instances.json')
    order = [dict(condition='scratch', task=t['id'], prefix='', version=v, repeat=1, limit=300000)
             for n, t in enumerate(tasks) for v in (VERSIONS if n % 2 == 0 else VERSIONS[::-1])]
    for repeat in (1, 2):
        for n, prefix in enumerate(source_prefixes):
            limit = next(r['limit'] for r in probes['r5']['prefixes'] if r['id'] == prefix['id'])
            for condition in ('pressure', 'roomy'):
                for version in (VERSIONS if (n + repeat) % 2 else VERSIONS[::-1]):
                    order.append(dict(condition=condition, task=prefix['task'], prefix=prefix['id'],
                                      version=version, repeat=repeat, limit=limit if condition == 'pressure' else 300000))
    harness = ten.harness_hashes() | {str(p.relative_to(REPO)): digest(p) for p in (
        Path(__file__), HERE / 'PROTOCOL.md', HERE / 'PREFIXES.json')}
    for relative in harness:
        target = OUT / 'original-harness' / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / relative, target)
    frozen = dict(order=order, sources={v: ten.hashes(HISTORY / v / 'src') for v in VERSIONS},
        historical_freezes={v: digest(HISTORY / v / 'freeze.json') for v in VERSIONS}, harness=harness,
        dataset_sha256=digest(ten.DATA / 'instances.json'), environment=ten.environment(),
        settings=probes['r5']['settings'], endpoint_sha256=probes['r5']['endpoint_sha256'],
        oracle_sha256=digest(OUT / 'oracle.json'), probes=probes, limits=ten.LIMITS)
    save(HERE / 'FREEZE.json', frozen)
    save(OUT / 'freeze.json', frozen)
    print('Frozen 44 real episodes: 20 scratch, 12 pressure, 12 roomy.', flush=True)


def inputs(version):
    frozen = read(OUT / 'freeze.json')
    assert ten.hashes(HISTORY / version / 'src') == frozen['sources'][version]
    assert digest(HISTORY / version / 'freeze.json') == frozen['historical_freezes'][version]
    assert ten.environment() == frozen['environment']
    assert digest(ten.DATA / 'instances.json') == frozen['dataset_sha256']
    assert digest(OUT / 'oracle.json') == frozen['oracle_sha256']
    for name, sha in frozen['harness'].items():
        assert digest(REPO / name) == digest(OUT / 'original-harness' / name) == sha
    for prefix in read(HERE / 'PREFIXES.json'):
        assert all(digest(REPO / name) == sha for name, sha in prefix['provenance'].items())
    base = ten.shared.base(HISTORY / version / 'src')
    config = base.settings('parallel')
    assert config.identity() == frozen['settings']
    assert hashlib.sha256(config.base_url.encode()).hexdigest() == frozen['endpoint_sha256']
    return frozen, base, config


async def episode(index):
    frozen = read(OUT / 'freeze.json')
    planned = frozen['order'][index]
    frozen, base, config = inputs(planned['version'])
    output = OUT / 'runs' / f'{index:02d}'
    root = OUT / 'workspaces' / f'{index:02d}'
    output.mkdir(parents=True, exist_ok=False)
    save(output / 'launch.json', planned | dict(freeze_sha256=digest(OUT / 'freeze.json')))
    task = next(t for t in ten.cases.bank(base) if t['id'] == planned['task'])
    checked = next(r for r in read(OUT / 'oracle.json') if r['task'] == planned['task'])
    prefix = next((p for p in read(HERE / 'PREFIXES.json') if p['id'] == planned['prefix']), None)
    base.ledger_driver.LIMITS = ten.LIMITS | {'reported_tokens': planned['limit']}
    ledger = base.ledger_driver.Ledger(f'{index:02d}', output)
    runtime, worker, start = await setup(base, config, task, root, planned['limit'], prefix, ledger)
    assert base.ledger_driver.product_hashes(root) == checked['public_hashes']
    save(output / 'start.json', start)

    async def execute():
        worker.future.cancel()
        worker.future = asyncio.create_task(runtime.workers._run(worker.runtime, worker.inbox))
        if prefix:
            await worker.future
        await runtime.run()

    started, error = time.monotonic(), None
    try:
        with base.observe(ledger, runtime):
            original = base.ModelClient.complete

            async def checked_request(client, messages, tools, *, on_event=None):
                if client is worker.runtime.model and start['child_tools'] is None:
                    start.update(child_tools=len(tools), child_estimate=worker.runtime._request_estimate,
                                 reserve=runtime.workers.budget.token_reserve(),
                                 parent_estimate=runtime._request_estimate,
                                 handoff_reason=worker.runtime._handoff_reason)
                    save(output / 'start.json', start)
                    tight = planned['condition'] == 'pressure' and planned['version'] == 'r6'
                    assert bool(tools) != tight, 'unexpected_first_child_tools'
                    assert (start['handoff_reason'] == 'team_token_limit') == tight
                return await original(client, messages, tools, on_event=on_event)

            base.ModelClient.complete = checked_request
            try:
                await asyncio.wait_for(execute(), timeout=600)
            finally:
                base.ModelClient.complete = original
    except (Exception, asyncio.CancelledError) as exc:
        error = type(exc).__name__
    finally:
        await cleanup(runtime, worker)
    runs, tools, _, usage = base.ledger_driver.collect(runtime, set())
    score = ten.grade(base, runtime.state.final, checked['gold'], task['kind'] == 'review')
    intact = base.ledger_driver.product_hashes(root) == checked['public_hashes']
    source_intact = ten.hashes(HISTORY / planned['version'] / 'src') == frozen['sources'][planned['version']]
    known = Counter()
    for request in ledger.requests:
        known.update(request.get('usage', {}))
    attempts = [a for r in ledger.requests for a in r['attempts']]
    children = []
    for run in runs:
        if run['parent_run_id']:
            handoff = read(root / '.aster/runs' / run['run_id'] / 'handoff.json')
            children.append(dict(run_id=run['run_id'], handoff=handoff))
    result = planned | dict(freeze_sha256=digest(OUT / 'freeze.json'), start=start, error=error,
        seconds=time.monotonic() - started, phase=runtime.state.phase, stop_reason=runtime.state.stop_reason,
        final=runtime.state.final, grade=score,
        behavior_correct=sum(c['observed'] and c['regression'] for c in score.get('checks', [])),
        passed=score['passed'] and runtime.state.phase == 'completed' and intact and source_intact and error is None,
        workspace_unchanged=intact, source_unchanged=source_intact, usage=usage,
        ledger_usage=dict(known), usage_matches=usage == dict(known), model_calls=len(ledger.requests),
        requests_without_usage=sum('total_tokens' not in r.get('usage', {}) for r in ledger.requests),
        http_without_usage=sum('total_tokens' not in a.get('usage', {}) for a in attempts),
        response_models=dict(Counter(a.get('response_model', '') for a in attempts)),
        parent_model_calls=sum(r.get('role') == 'parent' for r in ledger.requests),
        workers_started=len(children), initial_child_handoff_reason=worker.runtime._handoff_reason,
        children=children, runs=runs, tools=tools)
    save(output / 'result.json', runtime.evidence.redactor.value(result))
    print(json.dumps({key: result[key] for key in ('condition', 'task', 'version', 'repeat', 'passed',
                     'behavior_correct', 'workers_started', 'initial_child_handoff_reason', 'usage', 'error')}), flush=True)


def completed(index, planned):
    output = OUT / 'runs' / f'{index:02d}'
    identity = planned | dict(freeze_sha256=digest(OUT / 'freeze.json'))
    result, launch = read(output / 'result.json'), read(output / 'launch.json')
    assert launch == identity and all(result[k] == v for k, v in identity.items())
    return result


def run():
    frozen = read(OUT / 'freeze.json')
    for index, planned in enumerate(frozen['order']):
        output = OUT / 'runs' / f'{index:02d}'
        if (output / 'result.json').exists():
            completed(index, planned)
            continue
        assert not output.exists(), 'unfinished_episode_requires_audit'
        subprocess.run([sys.executable, str(__file__), 'episode', '--index', str(index)], check=True)
        result = completed(index, planned)
        if result['model_calls'] and result['requests_without_usage'] == result['model_calls']:
            raise RuntimeError('provider_unavailable; first failure retained')
    print('All 44 scheduled episodes complete.', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('freeze', 'probe', 'episode', 'run'))
    parser.add_argument('--version', choices=VERSIONS)
    parser.add_argument('--index', type=int)
    args = parser.parse_args()
    if args.command == 'freeze':
        freeze()
    elif args.command == 'probe':
        asyncio.run(probe(args.version))
    elif args.command == 'episode':
        asyncio.run(episode(args.index))
    else:
        run()
