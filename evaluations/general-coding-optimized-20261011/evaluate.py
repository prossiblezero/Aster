"""One fresh Aster attempt per task, with a frozen optimized source snapshot."""
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
NETWORK = 'aster-general-optimized-20261011'
RETRY = HERE.parent / 'general-coding-retry-20261010'
MODES = {'pytest-dev__pytest-10356': 'pytest', 'pytest-dev__pytest-7571': 'pytest',
         'terminal-kv-store-grpc': 'grpc'}


def imported(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


baseline = imported('retry_baseline', ORIGINAL / 'evaluate.py')
load, save, hashes = baseline.load, baseline.save, baseline.hashes


def check_sources():
    frozen = load(HERE / 'manifest.json')
    assert hashes(PRIVATE / 'runtime') == frozen['runtime_hashes']
    assert hashes(REPO / 'src/aster') == frozen['product_hashes']
    assert hashes(PRIVATE / 'sources/terminal-tasks') == frozen['terminal_source_hashes']
    assert hashes(PRIVATE / 'sources/aider-tasks') == frozen['aider_source_hashes']
    for name, digest in frozen['runner_hashes'].items():
        assert hashlib.sha256((REPO / name).read_bytes()).hexdigest() == digest, name
    for name, digest in frozen['input_hashes'].items():
        assert hashlib.sha256((REPO / name).read_bytes()).hexdigest() == digest, name
    return frozen


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
    original = load(ORIGINAL / 'manifest.json')
    assert hashes(BASE / 'runtime') == original['runtime_hashes']
    assert hashlib.sha256((BASE / 'tasks.json').read_bytes()).hexdigest() == original['tasks_sha256']
    assert not any((PRIVATE / 'runs').glob('*/launch.json'))
    PRIVATE.mkdir(parents=True, exist_ok=True)
    for name in ('sources', 'task-python', 'pi', 'node-v22.22.0-linux-x64', 'preflight'):
        target = PRIVATE / name
        if not target.exists():
            target.symlink_to(BASE / name, target_is_directory=True)
    shutil.copytree(BASE / 'runtime', PRIVATE / 'runtime', dirs_exist_ok=True)
    shutil.copytree(REPO / 'src/aster', PRIVATE / 'runtime/aster', dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copy2(RETRY / 'retry_environment.py', PRIVATE / 'runtime/retry_environment.py')
    shutil.copy2(HERE / 'execution_environment.py', PRIVATE / 'runtime/execution_environment.py')
    shutil.copy2(HERE / 'preflight.py', PRIVATE / 'runtime/preflight.py')
    entry = (BASE / 'runtime/aster_episode.py').read_text()
    entry = entry.replace('    settings = Settings(',
        "    import retry_environment\n    retry_environment.install(os.environ.get('BENCH_RETRY_MODE', ''))\n"
        '    settings = Settings(')
    (PRIVATE / 'runtime/aster_episode.py').write_text(entry)
    shutil.copy2(BASE / 'tasks.json', PRIVATE / 'tasks.json')
    shutil.copy2(BASE / 'repository-dataset.json', PRIVATE / 'repository-dataset.json')
    source_diff = subprocess.check_output(['git', 'diff', '--', 'src/aster', 'tests'], cwd=REPO)
    (HERE / 'source.patch').write_bytes(source_diff)
    (PRIVATE / 'relay').mkdir(exist_ok=True)
    runners = dict(original['runner_hashes'])
    for path in [*HERE.glob('*.py'), RETRY / 'retry_environment.py']:
        runners[str(path.relative_to(REPO))] = hashlib.sha256(path.read_bytes()).hexdigest()
    inputs = [ORIGINAL / 'results.json', ORIGINAL / 'manifest.json', ORIGINAL / 'preflight.json',
              RETRY / 'combined/results.json',
              PRIVATE / 'tasks.json', PRIVATE / 'repository-dataset.json',
              HERE / 'source.patch', HERE / 'PROTOCOL.md']
    save(HERE / 'manifest.json', {
        'aster_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=REPO, text=True).strip(),
        'source_status': 'base commit plus frozen source.patch; product_hashes are authoritative',
        'product_hashes': hashes(REPO / 'src/aster'),
        'model': 'gpt-5.6-luna', 'reasoning': 'medium', 'seconds_per_task': 900,
        'context_tokens': 64000, 'output_tokens': 8192,
        'attempts_per_task': 1, 'concurrency': 2,
        'tasks': [t['id'] for t in load(PRIVATE / 'tasks.json')], 'environment_modes': MODES,
        'runtime_hashes': hashes(PRIVATE / 'runtime'),
        'terminal_source_hashes': hashes(PRIVATE / 'sources/terminal-tasks'),
        'aider_source_hashes': hashes(PRIVATE / 'sources/aider-tasks'),
        'runner_hashes': runners,
        'input_hashes': {str(p.relative_to(REPO)): hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs},
        'score_policy': '20 fresh attempts; no replacement or best-of; pi and codex historical scores frozen',
        'evaluation_role': 'development-set evaluation after inspecting previous failures'})
    print('Prepared 20 fresh attempts; no model calls.')


def preflight():
    drv = driver()
    results = []
    for task in load(PRIVATE / 'tasks.json'):
        if task['id'] not in MODES and task['id'] != 'django__django-11400':
            continue
        client, container = drv.start_container(task, 'preflight-' + task['id'])
        try:
            env = {**drv.environment(task, 'preflight'), 'PYTHONPATH': '/opt/agent-code'}
            result = container.exec_run(['/home/dzh/.conda/envs/aster/bin/python',
                '/opt/agent-code/preflight.py'], environment=env, workdir=task['workspace'])
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
        print('Optimized model gateway ready', flush=True)
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
    parser.add_argument('mode', choices=('prepare', 'preflight', 'gateway', 'run'))
    args = parser.parse_args()
    if args.mode == 'preflight':
        import docker
        client = docker.from_env()
        network = client.networks.create(NETWORK, internal=True)
        try:
            preflight()
        finally:
            network.remove()
            client.close()
    else:
        {'prepare': prepare, 'gateway': gateway, 'run': run}[args.mode]()
