"""Frozen Aster cost pilot and full evaluation using the existing independent scorers."""
import argparse
import concurrent.futures
import difflib
import hashlib
import importlib.util
import os
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path
from socketserver import ThreadingMixIn, UnixStreamServer

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
ORIGINAL = ROOT.parent / 'general-coding-20261010'
BASE = REPO / 'artifacts/private' / ORIGINAL.name
WORK = REPO / 'artifacts/private' / ROOT.name
PREVIOUS = REPO / 'artifacts/private/general-coding-optimized-v2-20261011'
PYTHON = '/home/dzh/.conda/envs/aster/bin/python'
BWRAP = REPO / 'artifacts/private/general-coding-path-retry-20261011/sandbox/root/usr/bin/bwrap'
PHASES = ('pilot-baseline', 'pilot-candidate', 'full-candidate')
PILOT = ('psf__requests-5414', 'terminal-custom-memory-heap-crash', 'sphinx-doc__sphinx-11510')
HERE = PRIVATE = NETWORK = None


def imported(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


baseline = imported('cost_original', ORIGINAL / 'evaluate.py')
load, save, hashes = baseline.load, baseline.save, baseline.hashes


def configure(phase):
    global HERE, PRIVATE, NETWORK
    HERE, PRIVATE = ROOT / phase, WORK / phase
    NETWORK = 'aster-cost-' + phase


def check_sources():
    frozen = load(HERE / 'manifest.json')
    assert hashes(PRIVATE / 'runtime') == frozen['runtime_hashes']
    assert hashes(PRIVATE / 'runtime/aster') == frozen['product_hashes']
    for name, digest in frozen['input_hashes'].items():
        assert hashlib.sha256((REPO / name).read_bytes()).hexdigest() == digest, name
    return frozen


def prepare():
    assert not (WORK / 'candidate/aster').exists(), 'Already frozen'
    shutil.copytree(REPO / 'src/aster', WORK / 'candidate/aster',
                    ignore=shutil.ignore_patterns('__pycache__'))
    tasks = load(PREVIOUS / 'tasks.json')
    patch = []
    for p in sorted((WORK / 'candidate/aster').rglob('*.py')):
        rel = p.relative_to(WORK / 'candidate/aster')
        old = WORK / 'baseline/aster' / rel
        patch.extend(difflib.unified_diff(old.read_text().splitlines(True), p.read_text().splitlines(True),
            fromfile='baseline/src/aster/' + str(rel), tofile='candidate/src/aster/' + str(rel)))
    (ROOT / 'source.patch').write_text(''.join(patch))
    inputs = [*ROOT.glob('*.py'), ROOT / 'PROTOCOL.md', ROOT / 'source.patch', BWRAP,
              ORIGINAL / 'evaluate.py', ORIGINAL / 'summarize.py',
              ROOT.parent / 'general-coding-optimized-v2-20261011/evaluate.py',
              ROOT.parent / 'general-coding-optimized-v2-20261011/summarize.py',
              *[baseline.ORIGINAL / (name + '.py') for name in ('compare', 'gateway', 'relay')]]
    for phase in PHASES:
        configure(phase)
        source = WORK / ('baseline' if phase.endswith('baseline') else 'candidate') / 'aster'
        shutil.copytree(PREVIOUS / 'runtime', PRIVATE / 'runtime', ignore=shutil.ignore_patterns('__pycache__'))
        shutil.rmtree(PRIVATE / 'runtime/aster')
        shutil.copytree(source, PRIVATE / 'runtime/aster')
        shutil.copy2(ROOT / 'preflight.py', PRIVATE / 'runtime/cost_preflight.py')
        for name in ('sources', 'preflight', 'task-python', 'pi', 'node-v22.22.0-linux-x64'):
            (PRIVATE / name).symlink_to((PREVIOUS / name).resolve(), target_is_directory=True)
        selected = tasks if phase.startswith('full') else [next(t for t in tasks if t['id'] == task) for task in PILOT]
        save(PRIVATE / 'tasks.json', selected)
        shutil.copy2(BASE / 'repository-dataset.json', PRIVATE / 'repository-dataset.json')
        (PRIVATE / 'relay').mkdir()
        phase_inputs = inputs + [PRIVATE / 'tasks.json', PRIVATE / 'repository-dataset.json']
        phase_inputs += sorted((PRIVATE / 'sources').rglob('*.py'))
        save(HERE / 'manifest.json', {
            'aster_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=REPO, text=True).strip(),
            'model': 'gpt-5.6-luna', 'reasoning': 'medium', 'seconds_per_task': 900,
            'context_tokens': 64000, 'output_tokens': 8192, 'attempts_per_task': 1, 'concurrency': 2,
            'phase': phase, 'tasks': [t['id'] for t in selected],
            'product_hashes': hashes(source), 'runtime_hashes': hashes(PRIVATE / 'runtime'),
            'input_hashes': {str(p.relative_to(REPO)): hashlib.sha256(p.read_bytes()).hexdigest() for p in phase_inputs},
            'score_policy': 'one fresh attempt per task and phase; no replacement or best-of',
            'evaluation_role': 'development-set cost evaluation; pilot separate from full run',
        })
    print('Frozen baseline/candidate, six pilot attempts and twenty full attempts.', flush=True)


def driver():
    engine = imported('cost_v2', ROOT.parent / 'general-coding-optimized-v2-20261011/evaluate.py')
    engine.PRIVATE, engine.NETWORK, engine.check_sources = PRIVATE, NETWORK, check_sources
    drv = engine.driver()

    def start(task, job):
        import docker
        client = docker.from_env(timeout=1000)
        volumes = {str(PRIVATE / 'runtime'): {'bind': '/opt/agent-code', 'mode': 'ro'},
                   str(Path(PYTHON).parent.parent): {'bind': str(Path(PYTHON).parent.parent), 'mode': 'ro'},
                   str((PRIVATE / 'task-python').resolve()): {'bind': '/opt/task-python', 'mode': 'ro'},
                   str(BWRAP): {'bind': '/usr/bin/bwrap', 'mode': 'ro'},
                   str(Path('/usr/lib/x86_64-linux-gnu/libseccomp.so.2').resolve()):
                       {'bind': '/usr/lib/x86_64-linux-gnu/libseccomp.so.2', 'mode': 'ro'}}
        container = client.containers.run(task['image_id'], 'tail -f /dev/null', detach=True,
            name='aster-cost-' + HERE.name + '-' + job, network=NETWORK, volumes=volumes,
            working_dir=task['workspace'], user='root', nano_cpus=4_000_000_000,
            mem_limit='8g', pids_limit=512, cap_add=['SYS_ADMIN'],
            security_opt=['apparmor=unconfined', 'seccomp=unconfined'])
        try:
            helper = drv.module('compare')
            helper.checked(container, ['mkdir', '-p', '/agent-state/pi', '/root/.codex'])
            helper.checked(container, ['bwrap', '--ro-bind', '/', '/', '--', 'true'])
            if task['kind'] == 'repository':
                helper.checked(container, ['bash', '-c', 'git reset --hard ' + shlex.quote(task['base_commit'])
                    + ' && git clean -fd && rm -rf .git && git init -q && git add .'
                    ' && git -c user.name=Benchmark -c user.email=benchmark@localhost commit -qm base'])
            elif task['kind'] == 'small':
                for p in (PRIVATE / 'sources/aider-tasks' / task['task_name']).glob('*.py'):
                    helper.put(container, '/app/' + p.name, p.read_bytes())
            if engine.MODES.get(task['id']) == 'pytest':
                site = helper.checked(container, [task['test_python'], '-c',
                    'import site; print(site.getsitepackages()[0])']).decode().strip()
                helper.put(container, site + '/aster_benchmark_workspace.pth',
                           'import os, sys; sys.path.insert(0, os.path.join(os.getcwd(), "src"))\n')
                helper.checked(container, ['git', 'add', '-f', 'src/_pytest/_version.py'])
                helper.checked(container, ['git', '-c', 'user.name=Benchmark',
                    '-c', 'user.email=benchmark@localhost', 'commit', '--amend', '--no-edit', '-q'])
            return client, container
        except BaseException:
            container.remove(force=True)
            client.close()
            raise
    drv.start_container = start
    return drv


def preflight(drv):
    results = []
    for task in load(PRIVATE / 'tasks.json'):
        client, container = drv.start_container(task, 'preflight-' + task['id'])
        try:
            result = container.exec_run([PYTHON, '/opt/agent-code/cost_preflight.py'],
                environment={**drv.environment(task, 'preflight'), 'PYTHONPATH': '/opt/agent-code'})
            (PRIVATE / ('preflight-' + task['id'] + '.log')).write_bytes(result.output)
            assert result.exit_code == 0, result.output.decode(errors='replace')[-2000:]
            results.append({'task': task['id'], **__import__('json').loads(result.output)})
            print('Preflight passed:', task['id'], flush=True)
        finally:
            container.remove(force=True)
            client.close()
    save(HERE / 'environment-preflight.json', {'ready': True, 'checks': results})


def gateway():
    module = baseline.module('gateway')
    module.PRIVATE = PRIVATE
    assert module.SETTINGS.model == 'gpt-5.6-luna'
    class Server(ThreadingMixIn, UnixStreamServer):
        daemon_threads = True
    with Server(str(PRIVATE / 'relay/model.sock'), module.Gateway) as server:
        os.chmod(PRIVATE / 'relay/model.sock', 0o666)
        server.serve_forever()


def run(preflight_only=False):
    import docker
    check_sources()
    drv = driver()
    client = docker.from_env()
    network = client.networks.create(NETWORK, internal=True)
    relay = process = None
    socket = PRIVATE / 'relay/model.sock'
    try:
        if not (HERE / 'environment-preflight.json').exists():
            preflight(drv)
        if preflight_only:
            return
        with (PRIVATE / 'gateway.log').open('ab') as log:
            process = subprocess.Popen([PYTHON, str(ROOT / 'evaluate.py'), 'gateway', HERE.name],
                env={**os.environ, 'PYTHONPATH': str(REPO / 'src')}, stdout=log, stderr=log)
        for _ in range(100):
            if socket.exists():
                break
            assert process.poll() is None, 'Gateway exited'
            time.sleep(0.1)
        assert socket.exists()
        task = load(PRIVATE / 'tasks.json')[0]
        relay = client.containers.run(task['image_id'], [PYTHON, '/relay.py'], detach=True,
            name='aster-bench-gateway', network=NETWORK,
            volumes={str(PRIVATE / 'relay'): {'bind': '/relay', 'mode': 'rw'},
                     str(baseline.ORIGINAL / 'relay.py'): {'bind': '/relay.py', 'mode': 'ro'},
                     str(Path(PYTHON).parent.parent): {'bind': str(Path(PYTHON).parent.parent), 'mode': 'ro'}})
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(drv.episode, 'aster', t) for t in load(PRIVATE / 'tasks.json')]
            for future in concurrent.futures.as_completed(futures):
                future.result()
        check_sources()
    finally:
        if relay is not None:
            relay.remove(force=True)
        if process is not None:
            process.terminate()
            process.wait(timeout=10)
        socket.unlink(missing_ok=True)
        network.remove()
        client.close()


def summarize(mode):
    sys.modules['evaluate'] = sys.modules[__name__]
    summary = imported('cost_summary', ROOT.parent / 'general-coding-optimized-v2-20261011/summarize.py')
    summary.RUN_ID = 'general-cost-' + HERE.name + '-20261011'
    if mode == 'grade':
        summary.export()
        with (PRIVATE / 'grading.log').open('ab') as log:
            subprocess.run([sys.executable, '-m', 'swebench.harness.run_evaluation',
                '--dataset_name', str(PRIVATE / 'repository-dataset.json'),
                '--predictions_path', str(PRIVATE / 'aster.predictions.jsonl'),
                '--run_id', summary.RUN_ID, '--max_workers', '2', '--timeout', '900'],
                cwd=PRIVATE, stdout=log, stderr=log, check=True)
    if mode == 'status':
        summary.status()
        return
    summary.summarize()
    data = load(HERE / 'summary.json')
    data['planned'] = len(load(PRIVATE / 'tasks.json'))
    save(HERE / 'summary.json', data)
    audit = load(HERE / 'audit.json')
    audit['complete'] = data['finished'] == data['graded'] == data['planned']
    save(HERE / 'audit.json', audit)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('prepare', 'preflight', 'gateway', 'run', 'grade', 'results', 'status'))
    parser.add_argument('phase', choices=PHASES, nargs='?', default='pilot-candidate')
    args = parser.parse_args()
    configure(args.phase)
    if args.mode in ('grade', 'results', 'status'):
        summarize(args.mode)
    elif args.mode == 'preflight':
        run(preflight_only=True)
    else:
        {'prepare': prepare, 'gateway': gateway, 'run': run}[args.mode]()
