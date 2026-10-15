"""Frozen Aster-only old20 run using the existing general50 containers and scorers."""

import argparse
import concurrent.futures
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from metrics import measure, table

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
PRIVATE = REPO / 'artifacts/private' / HERE.name
BASE_HERE = HERE.parent / 'general-coding-50-20261012'
BASE = REPO / 'artifacts/private' / BASE_HERE.name
PYTHON = '/home/dzh/.conda/envs/aster/bin/python'
NETWORK = 'aster-context-cache-20261013'


def imported(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# general50 imports its adjacent prepare module; keep that implementation unchanged.
sys.path.insert(0, str(BASE_HERE))
engine = imported('context_cache_general50', BASE_HERE / 'evaluate.py')
load, save, hashes = engine.load, engine.save, engine.hashes


def verify():
    manifest = load(HERE / 'manifest.json')
    assert hashes(PRIVATE / 'runtime') == manifest['runtime_hashes'], 'Frozen runtime changed'
    assert hashes(PRIVATE / 'runtime/aster') == manifest['product_hashes'], 'Frozen source changed'
    for name, expected in manifest['input_hashes'].items():
        assert hashlib.sha256((REPO / name).read_bytes()).hexdigest() == expected, name
    return manifest


def configure():
    engine.HERE, engine.PRIVATE, engine.NETWORK = HERE, PRIVATE, NETWORK
    engine.verify_frozen = verify


def prepare():
    assert not (HERE / 'manifest.json').exists(), 'Already frozen; verify the existing snapshot'
    base_manifest = engine.verify_frozen()
    selected = [t for t in load(BASE / 'tasks.json') if t['exposure'] == 'old20']
    assert len(selected) == 20
    shutil.copytree(BASE / 'runtime', PRIVATE / 'runtime',
                    ignore=shutil.ignore_patterns('aster', '__pycache__'))
    shutil.copytree(REPO / 'src/aster', PRIVATE / 'runtime/aster',
                    ignore=shutil.ignore_patterns('__pycache__'))
    for name in ('sources', 'preflight', 'task-python', 'pi', 'node-v22.22.0-linux-x64'):
        (PRIVATE / name).symlink_to((BASE / name).resolve(), target_is_directory=True)
    shutil.copy2(BASE / 'repository-dataset.json', PRIVATE / 'repository-dataset.json')
    (PRIVATE / 'relay').mkdir()
    save(PRIVATE / 'tasks.json', selected)
    ordered = sorted(selected, key=lambda t: hashlib.sha256(('schedule:' + t['id']).encode()).hexdigest())
    save(HERE / 'schedule.json', [{'agent': 'aster', 'task': t['id']} for t in ordered])
    files = [*HERE.glob('*.py'), HERE / 'PROTOCOL.md', HERE / 'schedule.json',
             BASE_HERE / 'manifest.json', BASE_HERE / 'results.json',
             PRIVATE / 'tasks.json', PRIVATE / 'repository-dataset.json']
    inputs = dict(base_manifest['input_hashes'])
    inputs.update({str(p.relative_to(REPO)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files})
    save(HERE / 'manifest.json', {
        'aster_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=REPO, text=True).strip(),
        'source_policy': 'Current source plus context-cache v9 patch; immutable snapshot, not a clean commit',
        'model': 'gpt-5.6-luna', 'reasoning': 'medium', 'seconds_per_task': 900,
        'context_tokens': 64000, 'output_tokens': 8192, 'attempts_per_task': 1,
        'concurrency': 6, 'tasks': [t['id'] for t in ordered], 'planned_episodes': 20,
        'product_hashes': hashes(PRIVATE / 'runtime/aster'),
        'runtime_hashes': hashes(PRIVATE / 'runtime'), 'input_hashes': inputs,
    })
    verify()
    print('Frozen 20 Aster attempts. No model requests made.')


def run():
    import docker

    manifest = verify()
    configure()
    drv = engine.driver()
    by_id = {t['id']: t for t in load(PRIVATE / 'tasks.json')}
    tasks = [by_id[name] for name in manifest['tasks']]
    for task in tasks:
        out = PRIVATE / 'runs' / ('aster-' + task['id'])
        assert not (out / 'launch.json').exists() or (out / 'result.json').exists(), (
            'Incomplete launched attempt must be classified, never retried: ' + task['id'])
    client = docker.from_env()
    network = client.networks.create(NETWORK, internal=True)
    process = relay = None
    socket = PRIVATE / 'relay/model.sock'
    os.chdir(PRIVATE)
    try:
        with (PRIVATE / 'gateway.log').open('ab') as log:
            process = subprocess.Popen([PYTHON, str(HERE / 'evaluate.py'), 'gateway'],
                env={**os.environ, 'PYTHONPATH': str(PRIVATE / 'runtime')}, stdout=log, stderr=log)
        for _ in range(100):
            if socket.exists():
                break
            assert process.poll() is None, 'Gateway exited'
            time.sleep(.1)
        assert socket.exists()
        relay = client.containers.run(tasks[0]['image_id'], [PYTHON, '/relay.py'], detach=True,
            name='aster-bench-gateway', network=NETWORK,
            volumes={str(PRIVATE / 'relay'): {'bind': '/relay', 'mode': 'rw'},
                     str(engine.baseline.ORIGINAL / 'relay.py'): {'bind': '/relay.py', 'mode': 'ro'},
                     str(Path(PYTHON).parent.parent): {'bind': str(Path(PYTHON).parent.parent), 'mode': 'ro'}})

        def one(task):
            result = drv.episode('aster', task)
            if task['kind'] == 'repository':
                patch = (PRIVATE / 'runs' / result['job'] / 'prediction.patch').read_text()
                engine.repository_grade(task, 'aster', patch, HERE.name, skip_patch=not patch)
            return result

        usage_helpers = imported('context_cache_usage', HERE.parent / 'general-coding-20261010/summarize.py')
        outages, iterator = [], iter(tasks)
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            pending = {pool.submit(one, next(iterator)) for _ in range(6)}
            while pending:
                done, pending = concurrent.futures.wait(pending, return_when=concurrent.futures.FIRST_COMPLETED)
                for future in done:
                    result = future.result()
                    _, requests = usage_helpers.usage(PRIVATE / 'runs' / result['job'])
                    outages.append(bool(requests) and all(
                        r.get('error') in {'ReadTimeout', 'ConnectTimeout', 'ConnectError', 'RemoteProtocolError'}
                        or r.get('http_status', 200) >= 500 or r.get('http_status') == 429
                        or bool(r.get('stream_error')) for r in requests))
                if len(outages) >= 6 and all(outages[-6:]):
                    save(HERE / 'provider-outage.json', {'reason': 'six consecutive provider-only failures'})
                    raise RuntimeError('Provider outage; remaining tasks not submitted')
                for _ in done:
                    task = next(iterator, None)
                    if task is not None:
                        pending.add(pool.submit(one, task))
        verify()
    finally:
        if relay is not None:
            relay.remove(force=True)
        if process is not None:
            process.terminate()
            process.wait(timeout=10)
        socket.unlink(missing_ok=True)
        network.remove()
        client.close()


def results():
    manifest = verify()
    helpers = imported('context_cache_summary', HERE.parent / 'general-coding-20261010/summarize.py')
    rows = []
    for task in load(PRIVATE / 'tasks.json'):
        out = PRIVATE / 'runs' / ('aster-' + task['id'])
        if not (out / 'result.json').exists():
            continue
        row = load(out / 'result.json')
        usage, _ = helpers.usage(out)
        final, phase, stop = helpers.final_text(out, 'aster')
        row.update(usage, final=final, phase=phase, stop_reason=stop, exposure='old20')
        if task['kind'] == 'repository':
            path = PRIVATE / 'logs/evaluation' / HERE.name / 'aster' / task['id'] / 'report.json'
            grade = load(path).get(task['id']) if path.exists() else None
            row.update(grade=grade, graded=grade is not None, passed=bool(grade and grade.get('resolved')))
        else:
            row.update(graded='grade' in row, passed=bool(row.get('grade', {}).get('passed')))
        row['normal_delivery_and_passed'] = bool(row['passed'] and row['exit_code'] == 0 and final and phase == 'completed')
        rows.append(row)
    save(HERE / 'results.json', rows)
    cache = measure(PRIVATE / 'runs')
    save(HERE / 'metrics.json', cache)
    baseline_rows = [r for r in load(BASE_HERE / 'results.json') if r['exposure'] == 'old20']
    summary = {'planned': 20, 'finished': len(rows), 'graded': sum(r['graded'] for r in rows),
               'candidate_manifest': hashlib.sha256((HERE / 'manifest.json').read_bytes()).hexdigest(),
               'tasks': manifest['tasks'], 'agents': {}}
    for label, group in [('Aster v9', rows), *[(a, [r for r in baseline_rows if r['agent'] == a])
                                            for a in ('aster', 'pi', 'codex')]]:
        summary['agents'][label] = {key: sum(r.get(key, 0) for r in group) for key in (
            'passed', 'normal_delivery_and_passed', 'requests', 'input_tokens', 'cached_tokens',
            'output_tokens', 'total_tokens', 'missing_usage', 'seconds', 'timed_out')}
    save(HERE / 'summary.json', summary)
    lines = ['# Context cache v9：old20 结果', '',
             f"已完成 {len(rows)}/20，已评分 {summary['graded']}/20。单次尝试，未择优拼接。", '',
             '| Agent | 独立通过 | 正常交付且通过 | 请求数 | 总 token | 未缓存输入 | 秒 |',
             '|---|---:|---:|---:|---:|---:|---:|']
    for name, row in summary['agents'].items():
        lines.append(f"| {name} | {row['passed']}/20 | {row['normal_delivery_and_passed']}/20 | "
                     f"{row['requests']} | {row['total_tokens']} | "
                     f"{row['input_tokens'] - row['cached_tokens']} | {row['seconds']:.1f} |")
    lines += ['', 'Aster v9 为本次候选；其余为原 50 题中 old20 的冻结结果。上表含后台记忆用量。', '',
              '前台缓存指标（排除后台记忆）：', '', table(cache), '',
              '按 PROTOCOL.md 逐项验收，并核对保存的 context_built / model_reply 事件；'
              '缓存改善不自动代表通过率或成本目标达标。']
    (HERE / 'BENCHMARK.md').write_text('\n'.join(lines) + '\n')
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('prepare', 'verify', 'run', 'gateway', 'results'))
    args = parser.parse_args()
    if args.mode == 'gateway':
        configure()
        engine.gateway()
    elif args.mode == 'verify':
        verify()
        print('Frozen source and inputs verified. No model requests made.')
    else:
        globals()[args.mode]()
