"""Run paired Aster episodes from the frozen mixed-source dataset."""
# ruff: noqa: E402
from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib
import importlib.util
import json
import os
import subprocess
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / 'src'))
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / 'worker-memory-20260922'))
import run as ledger_driver

import aster
from aster.core.hooks import Hooks
from aster.core.runtime import Runtime
from aster.schema import TaskSpec

DATA = REPO / 'datasets/worker-code-v2'
OUT = REPO / 'artifacts/private/worker-code-20260928'
CACHE = REPO / 'artifacts/private/worker-code-v2/objects'
SOURCE = '0b8d867'
LIMITS = {
    'simple': dict(model_calls=40, admitted_tools=60, reported_tokens=500000, seconds=600),
    'medium': dict(model_calls=60, admitted_tools=90, reported_tokens=750000, seconds=900),
    'hard': dict(model_calls=80, admitted_tools=120, reported_tokens=1000000, seconds=1200),
}
COMMON = ('\n\nWork in the provided workspace and preserve the required behavior. '
          'Use Worker tools when available and useful for independent work; otherwise work directly. '
          'Choose any delegation and scope yourself. Verify the final implementation and report actual checks.')
save = ledger_driver.save


def bank():
    spec = importlib.util.spec_from_file_location('dataset_verify', DATA / 'checks/verify.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    tasks, _ = module.load()
    return tasks


def adapter(task):
    name = task['grading']['adapter']
    module = ('control_adapter' if name in {'aider_unittest', 'bigcode_unittest'} else
              'terminal_adapter' if name == 'terminal_bench_native' else 'repository_adapter')
    return importlib.import_module(module)


def loaded_source():
    if Path(aster.__file__).resolve() != REPO / 'src/aster/__init__.py':
        raise RuntimeError('wrong_aster_import')
    if sys.version_info[:2] != (3, 12) or 'aster' not in sys.prefix:
        raise RuntimeError('use_conda_aster_or_derived_python312')
    if subprocess.check_output(['git', 'diff', SOURCE, '--', 'src/aster'], cwd=REPO):
        raise RuntimeError('product_source_changed')
    return ledger_driver.hashes(REPO / 'src/aster', '**/*.py')


def settings(arm):
    return ledger_driver.config(max_workers=0 if arm == 'solo' else 3,
                                memory_enabled=False, memory_evolution=False, auto_dream=False)


def driver_hashes():
    paths = [HERE / 'evaluate.py', HERE / 'PROTOCOL.md', Path(ledger_driver.__file__),
             *HERE.glob('*_adapter.py'), HERE / 'execution_environment.py', DATA / 'manifest.json', DATA / 'instances.jsonl']
    snapshots = OUT / 'driver-snapshots'
    snapshots.mkdir(parents=True, exist_ok=True)
    hashes = {}
    for path in paths:
        content = path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        (snapshots / digest).write_bytes(content)
        hashes[str(path.relative_to(REPO))] = digest
    return hashes


def preflight(tasks):
    for task in tasks:
        destination = OUT / 'preflight' / task['id']
        resultfile = destination / 'preflight.json'
        if resultfile.exists():
            print('existing preflight', task['id'], flush=True)
            continue
        try:
            result = adapter(task).preflight(task, CACHE, destination)
        except Exception as exc:
            result = {'id': task['id'], 'ready': False, 'status': 'environment_unavailable',
                      'error_type': type(exc).__name__, 'detail': str(exc)[:1000]}
        save(resultfile, result)
        print(json.dumps({'preflight': task['id'], 'ready': result.get('ready'),
                          'status': result.get('status')}, ensure_ascii=False), flush=True)


async def episode(task, arm):
    source_hashes = loaded_source()
    name = task['id'] + '--' + arm
    output = OUT / 'runs' / name
    output.mkdir(parents=True, exist_ok=False)
    check = json.loads((OUT / 'preflight' / task['id'] / 'preflight.json').read_text())
    if not check['ready']:
        raise RuntimeError('preflight_gate_failed')
    root = OUT / 'workspaces' / name
    metadata = adapter(task).prepare(task, CACHE, root)
    if metadata.get('ready') is False:
        raise RuntimeError('workspace_preparation_failed')
    from execution_environment import install
    install(metadata)
    config = settings(arm)
    ledger_driver.LIMITS = LIMITS[task['difficulty']]
    ledger = ledger_driver.Ledger(name, output)
    prompt = task['prompt'] + COMMON + '\n\n' + metadata.get('prompt_suffix', '')
    runtime = Runtime(root, config, hooks=Hooks([ledger]))
    save(output / 'launch.json', {
        'name': name, 'task': task['id'], 'arm': arm, 'source_commit': SOURCE,
        'source_hashes': source_hashes, 'driver_hashes': driver_hashes(),
        'settings': config.identity(), 'endpoint_sha256': hashlib.sha256(config.base_url.encode()).hexdigest(),
        'limits': ledger_driver.LIMITS, 'prompt': prompt, 'preparation': metadata,
        'python': sys.executable, 'aster_import': aster.__file__, 'time': datetime.now(UTC).isoformat(),
    })
    before = ledger_driver.product_hashes(root)
    started = time.monotonic()
    error = None
    try:
        with ledger.observe():
            await asyncio.wait_for(runtime.run(TaskSpec(prompt=prompt,
                max_steps=ledger_driver.LIMITS['admitted_tools'], max_turns=ledger_driver.LIMITS['model_calls'],
                require_observation=True, require_edit=True, require_verification=True)),
                ledger_driver.LIMITS['seconds'])
    except (Exception, asyncio.CancelledError) as exc:
        error = type(exc).__name__
    finally:
        await runtime.workers.close()
        runtime.sandbox.close()
    seconds = time.monotonic() - started
    runs, tools, _, usage = ledger_driver.collect(runtime, set())
    known = Counter()
    for request in ledger.requests:
        known.update(request.get('usage', {}))
    grade_start = time.monotonic()
    try:
        grade = await asyncio.to_thread(adapter(task).grade, task, CACHE, root, output / 'grading')
    except Exception as exc:
        grade = {'passed': False, 'status': 'grader_error', 'error_type': type(exc).__name__, 'detail': str(exc)[:1000]}
    attempts = [a for r in ledger.requests for a in r['attempts']]
    children = [r for r in runs if r.get('parent_run_id')]
    state = runtime.state
    result = {'task': task['id'], 'source': task['source'], 'difficulty': task['difficulty'],
        'cohort': task['collaboration']['role'], 'arm': arm, 'passed': bool(grade.get('passed')),
        'grade': grade, 'error': error, 'phase': state.phase if state else None,
        'stop_reason': state.stop_reason if state else None, 'seconds': seconds,
        'grading_seconds': time.monotonic() - grade_start,
        'usage': usage, 'ledger_usage': dict(known), 'usage_matches': usage == dict(known),
        'model_calls': len(ledger.requests), 'http_attempts': len(attempts),
        'http_with_total_usage': sum('total_tokens' in a.get('usage', {}) for a in attempts),
        'response_models': dict(Counter(a.get('response_model', '') for a in attempts)),
        'diagnostic_flags': dict(Counter(f for a in attempts for f in a.get('flags', []))),
        'tool_calls': len(tools), 'tools': tools, 'runs': runs,
        'workers_started': len(children), 'workers_completed': sum(n['phase'] == 'completed' for n in children),
        'coordination_calls': sum(t['name'].startswith('worker_') for t in tools),
        'scope_conflicts': sum(t['reason'] == 'worker_scope_busy' for t in tools),
        'source_unchanged': source_hashes == loaded_source(),
        'product_before': before, 'product_after': ledger_driver.product_hashes(root),
        'final': state.final if state else '', 'workspace': str(root)}
    save(output / 'result.json', runtime.evidence.redactor.value(result))
    print(json.dumps({k: result[k] for k in ['task', 'arm', 'passed', 'seconds', 'workers_started', 'usage']}, ensure_ascii=False), flush=True)


def run_tasks(tasks):
    all_tasks = bank()
    for task in tasks:
        pf = OUT / 'preflight' / task['id'] / 'preflight.json'
        if not pf.exists() or not json.loads(pf.read_text()).get('ready'):
            print('environment_unavailable', task['id'], flush=True)
            continue
        index = next(i for i, t in enumerate(all_tasks) if t['id'] == task['id'])
        arms = ['solo', 'current_worker'] if index % 2 == 0 else ['current_worker', 'solo']
        for arm in arms:
            name = task['id'] + '--' + arm
            directory = OUT / 'runs' / name
            if (directory / 'result.json').exists():
                print('existing result', name, flush=True)
                continue
            if directory.exists():
                raise RuntimeError(f'incomplete_episode_requires_audit: {name}')
            log = OUT / 'logs' / (name + '.txt')
            log.parent.mkdir(parents=True, exist_ok=True)
            with log.open('w') as stream:
                proc = subprocess.run([sys.executable, str(HERE / 'evaluate.py'), 'episode', '--task', task['id'], '--arm', arm],
                    cwd=REPO, env=dict(os.environ, PYTHONPATH=str(REPO / 'src')), stdout=stream, stderr=subprocess.STDOUT)
            print('episode', name, 'exit', proc.returncode, flush=True)
            if proc.returncode:
                print('see private log:', log, flush=True)
                raise RuntimeError('episode_driver_failed')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['preflight', 'run', 'episode'])
    parser.add_argument('--task', action='append')
    parser.add_argument('--source', action='append')
    parser.add_argument('--arm', choices=['solo', 'current_worker'])
    args = parser.parse_args()
    loaded_source()
    tasks = [t for t in bank() if (not args.task or t['id'] in args.task) and (not args.source or t['source'] in args.source)]
    if not tasks:
        raise ValueError('no_selected_tasks')
    if args.mode == 'preflight':
        preflight(tasks)
    elif args.mode == 'run':
        run_tasks(tasks)
    else:
        if len(tasks) != 1 or not args.arm:
            raise ValueError('episode_requires_one_task_and_arm')
        asyncio.run(episode(tasks[0], args.arm))


if __name__ == '__main__':
    main()
