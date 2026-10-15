"""Run the one path-blocked interpreter task using a frozen current Aster."""
import argparse
import difflib
import hashlib
import importlib.util
import os
import shutil
import subprocess
import time
from pathlib import Path
from socketserver import ThreadingMixIn, UnixStreamServer

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
PRIVATE = REPO / 'artifacts/private' / HERE.name
PREVIOUS = REPO / 'artifacts/private/general-coding-optimized-v2-20261011'
TASK = 'terminal-schemelike-metacircular-eval'
NETWORK = 'aster-path-retry-20261011'
PYTHON = '/home/dzh/.conda/envs/aster/bin/python'


def imported(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


baseline = imported('path_retry_baseline', HERE.parent / 'general-coding-20261010/evaluate.py')
load, save, hashes = baseline.load, baseline.save, baseline.hashes


def check_sources():
    manifest = load(HERE / 'manifest.json')
    assert hashes(PRIVATE / 'runtime') == manifest['runtime_hashes']
    assert hashes(PRIVATE / 'runtime/aster') == manifest['product_hashes']
    for name, digest in manifest['input_hashes'].items():
        assert hashlib.sha256((REPO / name).read_bytes()).hexdigest() == digest, name
    return manifest


def start_container(task, job):
    import docker
    client = docker.from_env(timeout=1000)
    volumes = {
        str(PRIVATE / 'runtime'): {'bind': '/opt/agent-code', 'mode': 'ro'},
        str(Path(PYTHON).parent.parent): {'bind': str(Path(PYTHON).parent.parent), 'mode': 'ro'},
        str((PREVIOUS / 'task-python').resolve()): {'bind': '/opt/task-python', 'mode': 'ro'},
        str(PRIVATE / 'sandbox/root/usr/bin/bwrap'): {'bind': '/usr/bin/bwrap', 'mode': 'ro'},
        str(Path('/usr/lib/x86_64-linux-gnu/libseccomp.so.2').resolve()):
            {'bind': '/usr/lib/x86_64-linux-gnu/libseccomp.so.2', 'mode': 'ro'},
    }
    container = client.containers.run(
        task['image_id'], 'tail -f /dev/null', detach=True,
        name='aster-path-retry-' + job, network=NETWORK,
        volumes=volumes, working_dir=task['workspace'], user='root',
        nano_cpus=4_000_000_000, mem_limit='8g', pids_limit=512,
        cap_add=['SYS_ADMIN'], security_opt=['apparmor=unconfined', 'seccomp=unconfined'],
    )
    try:
        helper = baseline.module('compare')
        helper.checked(container, ['mkdir', '-p', '/agent-state/pi', '/root/.codex'])
        helper.checked(container, ['bwrap', '--ro-bind', '/', '/', '--', 'true'])
        helper.checked(container, [task['test_python'], '-c', 'import sys; print(sys.version)'])
        return client, container
    except BaseException:
        container.remove(force=True)
        client.close()
        raise


def driver():
    baseline.PRIVATE, baseline.NETWORK = PRIVATE, NETWORK
    baseline.verify_frozen = check_sources
    baseline.start_container = start_container
    environment = baseline.environment

    def task_environment(task, job):
        return {**environment(task, job), 'BENCH_CONTAINER_ISOLATION': 'docker'}

    baseline.environment = task_environment
    return baseline


def prepare():
    assert not (HERE / 'manifest.json').exists(), 'Already frozen'
    PRIVATE.mkdir(parents=True, exist_ok=True)
    task = next(t for t in load(PREVIOUS / 'tasks.json') if t['id'] == TASK)
    shutil.copytree(PREVIOUS / 'runtime', PRIVATE / 'runtime',
                    ignore=shutil.ignore_patterns('__pycache__'))
    shutil.rmtree(PRIVATE / 'runtime/aster')
    shutil.copytree(REPO / 'src/aster', PRIVATE / 'runtime/aster',
                    ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copy2(HERE / 'preflight.py', PRIVATE / 'runtime/path_preflight.py')
    for name in ('sources', 'preflight'):
        (PRIVATE / name).symlink_to((PREVIOUS / name).resolve(), target_is_directory=True)
    save(PRIVATE / 'tasks.json', [task])
    (PRIVATE / 'relay').mkdir()
    patch = []
    for path in sorted((PRIVATE / 'runtime/aster').rglob('*.py')):
        name = path.relative_to(PRIVATE / 'runtime/aster')
        old = PREVIOUS / 'runtime/aster' / name
        patch.extend(difflib.unified_diff(
            old.read_text().splitlines(keepends=True) if old.exists() else [],
            path.read_text().splitlines(keepends=True),
            fromfile='v2/src/aster/' + str(name), tofile='current/src/aster/' + str(name)))
    (HERE / 'source.patch').write_text(''.join(patch))
    inputs = [*HERE.glob('*.py'), HERE / 'PROTOCOL.md', HERE / 'source.patch',
              PRIVATE / 'tasks.json', PRIVATE / 'sandbox/bubblewrap.deb',
              PRIVATE / 'sandbox/root/usr/bin/bwrap',
              baseline.HERE / 'evaluate.py', baseline.HERE / 'summarize.py',
              *[baseline.ORIGINAL / (name + '.py') for name in ('compare', 'gateway', 'relay')],
              HERE.parent / 'general-coding-optimized-v2-20261011/results.json',
              HERE.parent / 'general-coding-optimized-v2-20261011/manifest.json']
    inputs += sorted((PRIVATE / 'sources/terminal-tasks' / task['task_name'] / 'tests').rglob('*.py'))
    save(HERE / 'manifest.json', {
        'aster_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=REPO, text=True).strip(),
        'source_status': 'frozen product_hashes; source.patch is relative to optimized v2',
        'model': 'gpt-5.6-luna', 'reasoning': 'medium', 'seconds_per_task': 900,
        'context_tokens': 64000, 'output_tokens': 8192,
        'attempts_per_task': 1, 'concurrency': 1, 'tasks': [TASK], 'image_id': task['image_id'],
        'product_hashes': hashes(PRIVATE / 'runtime/aster'),
        'runtime_hashes': hashes(PRIVATE / 'runtime'),
        'input_hashes': {str(p.relative_to(REPO)): hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs},
        'sandbox_package': 'https://deb.debian.org/debian/pool/main/b/bubblewrap/bubblewrap_0.8.0-2+deb12u1_amd64.deb',
        'container_permissions': {'cap_add': ['SYS_ADMIN'], 'security_opt': ['apparmor=unconfined', 'seccomp=unconfined']},
        'score_policy': 'one fresh selective retry; other nineteen v2 tasks and pi/codex remain frozen',
    })
    print('Frozen one interpreter task; no model calls.', flush=True)


def preflight(drv, task):
    client, container = drv.start_container(task, 'preflight')
    try:
        result = container.exec_run([PYTHON, '/opt/agent-code/path_preflight.py'],
            workdir=task['workspace'],
            environment={**drv.environment(task, 'preflight'), 'PYTHONPATH': '/opt/agent-code'})
        (PRIVATE / 'preflight.log').write_bytes(result.output)
        assert result.exit_code == 0, result.output.decode(errors='replace')[-3000:]
        import json
        save(HERE / 'environment-preflight.json', json.loads(result.output))
        print('Original interpreter commands and formal path verification passed.', flush=True)
    finally:
        container.remove(force=True)
        client.close()


def gateway():
    module = baseline.module('gateway')
    module.PRIVATE = PRIVATE
    assert module.SETTINGS.model == 'gpt-5.6-luna'

    class Server(ThreadingMixIn, UnixStreamServer):
        daemon_threads = True

    with Server(str(PRIVATE / 'relay/model.sock'), module.Gateway) as server:
        os.chmod(PRIVATE / 'relay/model.sock', 0o666)
        print('Path retry model gateway ready', flush=True)
        server.serve_forever()


def run():
    import docker
    check_sources()
    drv = driver()
    task = load(PRIVATE / 'tasks.json')[0]
    client = docker.from_env()
    network = client.networks.create(NETWORK, internal=True)
    relay = gateway_process = None
    socket = PRIVATE / 'relay/model.sock'
    try:
        preflight(drv, task)
        with (PRIVATE / 'gateway.log').open('ab') as log:
            gateway_process = subprocess.Popen([PYTHON, str(Path(__file__)), 'gateway'],
                env={**os.environ, 'PYTHONPATH': str(REPO / 'src')}, stdout=log, stderr=log)
        for _ in range(100):
            if socket.exists():
                break
            assert gateway_process.poll() is None, 'Gateway exited; inspect gateway.log'
            time.sleep(0.1)
        assert socket.exists()
        relay = client.containers.run(task['image_id'], [PYTHON, '/relay.py'],
            name='aster-bench-gateway', network=NETWORK, detach=True,
            volumes={str(PRIVATE / 'relay'): {'bind': '/relay', 'mode': 'rw'},
                     str(baseline.ORIGINAL / 'relay.py'): {'bind': '/relay.py', 'mode': 'ro'},
                     str(Path(PYTHON).parent.parent): {'bind': str(Path(PYTHON).parent.parent), 'mode': 'ro'}})
        drv.episode('aster', task)
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
