"""One Aster-only diagnostic retry for five previously affected tasks."""
import argparse
import concurrent.futures
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from socketserver import ThreadingMixIn, UnixStreamServer

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
ORIGINAL = HERE.parent / 'general-coding-20261010'
BASE = REPO / 'artifacts/private' / ORIGINAL.name
PRIVATE = REPO / 'artifacts/private' / HERE.name
NETWORK = 'aster-general-retry-20261010'
TASKS = ('terminal-custom-memory-heap-crash', 'terminal-cobol-modernization',
         'django__django-11400', 'pytest-dev__pytest-10356', 'terminal-kv-store-grpc')
MODES = {'pytest-dev__pytest-10356': 'pytest', 'terminal-kv-store-grpc': 'grpc'}


def imported(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


baseline = imported('retry_baseline', ORIGINAL / 'evaluate.py')
load, save, hashes = baseline.load, baseline.save, baseline.hashes


def check_sources():
    original = baseline.verify_frozen()
    frozen = load(HERE / 'manifest.json')
    assert hashes(PRIVATE / 'runtime') == frozen['runtime_hashes']
    assert {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in HERE.glob('*.py')} == frozen['retry_script_hashes']
    assert hashlib.sha256((ORIGINAL / 'results.json').read_bytes()).hexdigest() == frozen['original_results_sha256']
    return original


def driver():
    result = imported('retry_driver', ORIGINAL / 'evaluate.py')
    result.PRIVATE, result.NETWORK = PRIVATE, NETWORK
    result.verify_frozen = check_sources
    environment = result.environment

    def configured_environment(task, job):
        return {**environment(task, job), 'BENCH_RETRY_MODE': MODES.get(task['id'], '')}

    result.environment = configured_environment
    start = result.start_container

    def configured_start(task, job):
        client, container = start(task, job)
        try:
            if MODES.get(task['id']) == 'pytest':
                helper = result.module('compare')
                site = helper.checked(container, [task['test_python'], '-c',
                    'import site; print(site.getsitepackages()[0])']).decode().strip()
                helper.put(container, site + '/aster_benchmark_workspace.pth',
                    'import os, sys; sys.path.insert(0, os.path.join(os.getcwd(), "src"))\n')
                # Keep the image's existing generated version file in sandbox copies.
                # Amend only the disposable baseline so it is not part of the candidate patch.
                helper.checked(container, ['git', 'add', '-f', 'src/_pytest/_version.py'])
                helper.checked(container, ['git', '-c', 'user.name=Benchmark',
                    '-c', 'user.email=benchmark@localhost', 'commit', '--amend', '--no-edit', '-q'])
            return client, container
        except BaseException:
            container.remove(force=True)
            client.close()
            raise

    result.start_container = configured_start
    return result


def prepare():
    baseline.verify_frozen()
    assert not any((PRIVATE / 'runs').glob('*/launch.json'))
    PRIVATE.mkdir(parents=True, exist_ok=True)
    for name in ('sources', 'task-python', 'pi', 'node-v22.22.0-linux-x64', 'preflight'):
        target = PRIVATE / name
        if not target.exists():
            target.symlink_to(BASE / name, target_is_directory=True)
    shutil.copytree(BASE / 'runtime', PRIVATE / 'runtime', dirs_exist_ok=True)
    shutil.copy2(HERE / 'retry_environment.py', PRIVATE / 'runtime/retry_environment.py')
    entry = (BASE / 'runtime/aster_episode.py').read_text()
    entry = entry.replace('    settings = Settings(',
        "    import retry_environment\n    retry_environment.install(os.environ.get('BENCH_RETRY_MODE', ''))\n"
        '    settings = Settings(')
    (PRIVATE / 'runtime/aster_episode.py').write_text(entry)
    rows = {t['id']: t for t in load(BASE / 'tasks.json')}
    save(PRIVATE / 'tasks.json', [rows[name] for name in TASKS])
    save(PRIVATE / 'repository-dataset.json', [r for r in load(BASE / 'repository-dataset.json')
                                             if r['instance_id'] in TASKS])
    (PRIVATE / 'relay').mkdir(exist_ok=True)
    save(HERE / 'manifest.json', {
        'aster_commit': load(ORIGINAL / 'manifest.json')['aster_commit'],
        'model': 'gpt-5.6-luna', 'reasoning': 'medium', 'seconds_per_task': 900,
        'attempts_per_task': 1, 'concurrency': 2, 'tasks': TASKS, 'environment_modes': MODES,
        'runtime_hashes': hashes(PRIVATE / 'runtime'),
        'retry_script_hashes': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in HERE.glob('*.py')},
        'original_results_sha256': hashlib.sha256((ORIGINAL / 'results.json').read_bytes()).hexdigest(),
        'original_report_sha256': hashlib.sha256((ORIGINAL / 'REPORT.md').read_bytes()).hexdigest(),
        'score_policy': 'original 12/20 remains frozen; retries reported separately'})
    print('Prepared five separate retries; no model calls.')


def preflight():
    drv = driver()
    results = []
    for task in load(PRIVATE / 'tasks.json'):
        if task['id'] not in MODES:
            continue
        client, container = drv.start_container(task, 'preflight-' + task['id'])
        try:
            env = {**drv.environment(task, 'preflight'), 'PYTHONPATH': '/opt/agent-code'}
            result = container.exec_run(['/home/dzh/.conda/envs/aster/bin/python',
                '/opt/agent-code/retry_environment.py'], environment=env, workdir=task['workspace'])
            (PRIVATE / ('preflight-' + task['id'] + '.log')).write_bytes(result.output)
            assert result.exit_code == 0, result.output.decode(errors='replace')[-3500:]
            results.append(json.loads(result.output))
            print('Environment preflight passed:', task['id'], flush=True)
        finally:
            container.remove(force=True)
            client.close()
    save(HERE / 'environment-preflight.json', {'ready': True, 'checks': results,
        'runtime_hashes': hashes(PRIVATE / 'runtime')})


def gateway():
    module = baseline.module('gateway')
    module.PRIVATE = PRIVATE
    assert module.SETTINGS.model == 'gpt-5.6-luna'

    class Server(ThreadingMixIn, UnixStreamServer):
        daemon_threads = True

    with Server(str(PRIVATE / 'relay/model.sock'), module.Gateway) as server:
        os.chmod(PRIVATE / 'relay/model.sock', 0o666)
        print('Retry model gateway ready', flush=True)
        server.serve_forever()


def run():
    import docker
    check_sources()
    client = docker.from_env()
    network = client.networks.create(NETWORK, internal=True)
    relay = gateway_process = None
    socket = PRIVATE / 'relay/model.sock'
    try:
        preflight_path = HERE / 'environment-preflight.json'
        if (not preflight_path.exists()
                or load(preflight_path).get('runtime_hashes') != hashes(PRIVATE / 'runtime')):
            preflight()
        if socket.exists():
            socket.unlink()
        with (PRIVATE / 'gateway.log').open('ab') as log:
            gateway_process = subprocess.Popen([
                '/home/dzh/.conda/envs/aster/bin/python', str(Path(__file__)), 'gateway'],
                env={**os.environ, 'PYTHONPATH': str(REPO / 'src')}, stdout=log, stderr=log)
        for _ in range(100):
            if socket.exists():
                break
            assert gateway_process.poll() is None, 'Gateway exited; inspect gateway.log'
            time.sleep(0.1)
        assert socket.exists()
        task = load(PRIVATE / 'tasks.json')[0]
        relay = client.containers.run(task['image_id'],
            ['/home/dzh/.conda/envs/aster/bin/python', '/relay.py'],
            name='aster-bench-gateway', network=NETWORK, detach=True,
            volumes={str(PRIVATE / 'relay'): {'bind': '/relay', 'mode': 'rw'},
                     str(REPO / 'evaluations/swebench-compare-20261008/relay.py'): {'bind': '/relay.py', 'mode': 'ro'},
                     '/home/dzh/.conda/envs/aster': {'bind': '/home/dzh/.conda/envs/aster', 'mode': 'ro'}})
        drv = driver()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(drv.episode, 'aster', t) for t in load(PRIVATE / 'tasks.json')]
            for future in concurrent.futures.as_completed(futures):
                future.result()
        check_sources()
    finally:
        if relay is not None:
            relay.remove(force=True)
        if gateway_process is not None:
            gateway_process.terminate()
            gateway_process.wait(timeout=10)
        socket.unlink(missing_ok=True)
        network.remove()
        client.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('prepare', 'gateway', 'run'))
    args = parser.parse_args()
    {'prepare': prepare, 'gateway': gateway, 'run': run}[args.mode]()
