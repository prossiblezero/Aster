"""Run a git ref of Aster on chosen general-50 tasks with the frozen runner, gateway and graders.

prepare/verify/results never call a model; run does. Each named run exports src/aster from one
git ref into its own private runtime, so results map to an exact commit.
"""
import argparse
import concurrent.futures
import hashlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
PRIVATE = REPO / 'artifacts/private' / HERE.name
BASE_HERE = HERE.parent / 'general-coding-50-20261012'
BASE = REPO / 'artifacts/private' / BASE_HERE.name
TURN_HERE = HERE.parent / 'turn-reduction-20261015'
PYTHON = '/home/dzh/.conda/envs/aster/bin/python'

sys.path.insert(0, str(TURN_HERE))
from metrics import measure  # noqa: E402
from turn_metrics import measure as measure_turns  # noqa: E402


def imported(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sys.path.insert(0, str(BASE_HERE))
engine = imported('harness_v12_general50', BASE_HERE / 'evaluate.py')
load, save, hashes = engine.load, engine.save, engine.hashes


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def paths(name):
    return HERE / 'runs' / name, PRIVATE / name


def pool_dir(name):
    return REPO / 'artifacts/private' / name


def select(spec, pool=BASE):
    tasks = load(pool / 'tasks.json')
    if len(spec) == 1 and spec[0] in {t['pool'] for t in tasks if t.get('pool')}:
        chosen = [t for t in tasks if t.get('pool') == spec[0]]  # fresh30, fresh30b, ...
    elif spec in (['old20'], ['new30']):
        chosen = [t for t in tasks if t['exposure'] == spec[0] and 'pool' not in t]
    else:
        by_id = {t['id']: t for t in tasks}
        assert set(spec) <= set(by_id), sorted(set(spec) - set(by_id))
        chosen = [by_id[name] for name in spec]
    chosen.sort(key=lambda t: hashlib.sha256(('schedule:' + t['id']).encode()).hexdigest())
    return chosen


def prepare(name, ref, spec, pool=BASE.name, agent='aster', concurrency=6, model='gpt-5.6-luna',
            protocol='responses', key_env='ASTER_API_KEY', base_url_env='ASTER_BASE_URL'):
    base = pool_dir(pool)
    public, root = paths(name)
    assert not root.exists(), 'Run directory exists; choose a new name'
    commit = subprocess.check_output(['git', 'rev-parse', ref + '^{commit}'], cwd=REPO, text=True).strip()
    shutil.copytree(base / 'runtime', root / 'runtime', ignore=shutil.ignore_patterns('aster', '__pycache__'))
    archive = subprocess.check_output(['git', 'archive', commit, 'src/aster'], cwd=REPO)
    with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
        for item in bundle:
            if item.isfile():
                target = root / 'runtime/aster' / Path(item.name).relative_to('src/aster')
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(bundle.extractfile(item).read())
    patch_service_launcher(root / 'runtime' / 'execution_environment.py')
    patch_one_shot_driver(root / 'runtime' / 'aster_episode.py')
    if agent == 'aster':
        patch_model_driver(root / 'runtime' / 'aster_episode.py', model, protocol)
    else:
        assert (model, protocol) == ('gpt-5.6-luna', 'responses'), 'pi/Codex runs use the default model'
    for link in ('sources', 'preflight', 'task-python', 'pi', 'node-v22.22.0-linux-x64'):
        (root / link).symlink_to((base / link).resolve(), target_is_directory=True)
    shutil.copy2(base / 'repository-dataset.json', root / 'repository-dataset.json')
    tasks = select(spec, base)
    save(root / 'tasks.json', tasks)
    (root / 'relay').mkdir()
    public.mkdir(parents=True, exist_ok=True)
    save(public / 'manifest.json', {
        'name': name, 'ref': ref, 'aster_commit': commit, 'tasks': [t['id'] for t in tasks],
        'pool': pool, 'agent': agent,
        'model': model, 'protocol': protocol, 'key_env': key_env, 'base_url_env': base_url_env,
        'reasoning': 'medium', 'seconds_per_task': 900,
        'context_tokens': 64000, 'output_tokens': 8192, 'max_workers': 0, 'automatic_memory': True,
        'cpu': 4, 'memory_gib': 8, 'concurrency': concurrency,
        'product_hashes': hashes(root / 'runtime/aster'), 'runtime_hashes': hashes(root / 'runtime'),
        'runner_hashes': {str(p.relative_to(REPO)): sha(p) for p in
                          [HERE / 'evaluate.py', BASE_HERE / 'evaluate.py', TURN_HERE / 'metrics.py',
                           TURN_HERE / 'turn_metrics.py']},
    })
    print(f'Prepared {name} ({agent}) at {commit[:12]} with {len(tasks)} tasks from {pool}; no model requests made.')


SERVICE_PATCH = """
    original_spawn = getattr(SandboxRunner, '_spawn', None)
    if original_spawn is not None:
        def spawn(self, argv, cwd, log):
            # Long-running services use the same evaluation launcher as commands.
            if len(argv) > 2 and Path(argv[2]).name == 'linux.py':
                argv = [argv[0], argv[1], str(launcher), *argv[3:]]
            return original_spawn(self, argv, cwd, log)
        SandboxRunner._spawn = spawn
"""


def patch_service_launcher(path):
    """Evaluation glue rewrote only command launches; give service launches the same launcher."""
    text = path.read_text()
    marker = '    SandboxRunner.environment = staticmethod(environment)'
    if 'original_spawn' not in text:
        assert text.count(marker) == 1, 'unexpected execution_environment layout'
        path.write_text(text.replace(marker, SERVICE_PATCH.rstrip() + '\n' + marker))


def patch_one_shot_driver(path):
    """The frozen driver drained memory consolidation for 15 s after every episode, which
    started background model requests. Run each version as its own one-shot CLI does:
    versions with background_maintenance turn it off; older ones keep their 15 s drain."""
    text = path.read_text()
    if 'background_maintenance' in text:
        return
    settings_end = "completion='strict', max_workers=0)"
    drain = '        await runtime.maintenance.drain(timeout=15)'
    assert text.count(settings_end) == 1 and text.count(drain) == 1, 'unexpected aster_episode layout'
    text = text.replace(settings_end, "completion='strict', max_workers=0,\n"
                        "                        **({'background_maintenance': False}\n"
                        "                           if 'background_maintenance' in Settings.model_fields else {}))")
    text = text.replace(drain, "        if getattr(runtime.settings, 'background_maintenance', True):\n"
                        "            await runtime.maintenance.drain(timeout=15)")
    path.write_text(text)


def patch_model_driver(path, model, protocol):
    """Run Aster against another model family; chat is the protocol local servers speak."""
    text = path.read_text()
    assert text.count("model='gpt-5.6-luna'") == 1 and text.count("stream=True") == 1, 'unexpected aster_episode layout'
    text = text.replace("model='gpt-5.6-luna'", f"model={model!r}")
    if protocol == 'chat':
        text = text.replace("stream=True", "stream=False, protocol='chat'")
    path.write_text(text)


def chat_usage(usage):
    """Chat Completions usage in the Responses shape the summaries read."""
    if not usage:
        return None
    return {'input_tokens': usage.get('prompt_tokens', 0), 'output_tokens': usage.get('completion_tokens', 0),
            'total_tokens': usage.get('total_tokens', 0),
            'input_tokens_details': {'cached_tokens': (usage.get('prompt_tokens_details') or {}).get(
                'cached_tokens', usage.get('prompt_cache_hit_tokens', 0))},
            'output_tokens_details': {'reasoning_tokens': (usage.get('completion_tokens_details') or {}).get('reasoning_tokens', 0)}}


def serve_gateway(name):
    """The shared gateway, with this run's model and key, plus non-streaming Chat Completions."""
    import hashlib as _hashlib
    import re as _re
    import time as _time
    import uuid
    from socketserver import ThreadingMixIn, UnixStreamServer

    import httpx
    from pydantic import SecretStr

    from aster.config import read_env

    public, root = paths(name)
    manifest = load(public / 'manifest.json')
    module = engine.baseline.module('gateway')
    module.PRIVATE = root
    env = {**read_env(REPO / '.env'), **os.environ}
    key = env[manifest.get('key_env', 'ASTER_API_KEY')]
    base_url = env[manifest.get('base_url_env', 'ASTER_BASE_URL')]
    module.SETTINGS = module.SETTINGS.model_copy(
        update={'model': manifest['model'], 'api_key': SecretStr(key), 'base_url': base_url})
    settings = module.SETTINGS

    class Gateway(module.Gateway):
        def do_POST(self):
            match = _re.fullmatch(r"/([a-z0-9_-]+)/v1/chat/completions", self.path)
            if not match:
                return super().do_POST()
            job = root / 'runs' / match[1]
            if not (job / 'launch.json').exists():
                self.send_error(403)
                return
            payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            original = {k: payload.get(k) for k in ('model', 'max_tokens', 'temperature', 'top_p', 'stream')}
            payload.update(model=settings.model, max_tokens=8192, stream=False)
            request_id = uuid.uuid4().hex
            archive = job / 'provider'
            archive.mkdir(exist_ok=True)
            encoded = json.dumps(payload).encode()
            (archive / (request_id + '.request.json')).write_bytes(encoded)
            started = _time.monotonic()
            row = {'request_id': request_id, 'started': _time.time(), 'original_parameters': original,
                   'model': settings.model, 'protocol': 'chat', 'max_output_tokens': 8192,
                   'request_sha256': _hashlib.sha256(encoded).hexdigest(), 'usage': None}
            try:
                with httpx.Client(timeout=httpx.Timeout(300, connect=20)) as client:
                    response = client.post(settings.base_url.rstrip('/') + '/chat/completions', content=encoded,
                                           headers={'Authorization': 'Bearer ' + settings.api_key.get_secret_value(),
                                                    'Content-Type': 'application/json'})
                body = response.content
                (archive / (request_id + '.response')).write_bytes(body)
                row['http_status'] = response.status_code
                try:
                    data = json.loads(body)
                    row['usage'] = chat_usage(data.get('usage'))
                    row['response_model'] = data.get('model')
                    row['terminal'] = (data.get('choices') or [{}])[0].get('finish_reason')
                except ValueError:
                    pass
                self.send_response(response.status_code)
                self.send_header('Content-Type', response.headers.get('content-type', 'application/json'))
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Connection', 'close')
                self.end_headers()
                self.wfile.write(body)
            except Exception as exc:
                row['error'] = type(exc).__name__
                self.send_error(502, 'Upstream unavailable')
            finally:
                row['seconds'] = round(_time.monotonic() - started, 3)
                (archive / (request_id + '.json')).write_text(json.dumps(row, indent=2) + '\n')
                self.close_connection = True

    class Server(ThreadingMixIn, UnixStreamServer):
        daemon_threads = True

    with Server(str(root / 'relay/model.sock'), Gateway) as server:
        os.chmod(root / 'relay/model.sock', 0o666)
        server.serve_forever()


def verify(name):
    public, root = paths(name)
    manifest = load(public / 'manifest.json')
    assert hashes(root / 'runtime/aster') == manifest['product_hashes'], 'Frozen source changed'
    assert hashes(root / 'runtime') == manifest['runtime_hashes'], 'Frozen runtime changed'
    return manifest


def configure(name):
    public, root = paths(name)
    engine.HERE, engine.PRIVATE = HERE, root
    engine.NETWORK = 'aster-v12-' + name
    engine.verify_frozen = lambda: verify(name)
    return public, root


def run(name):
    import docker

    global RUN_SUFFIX
    manifest = verify(name)
    agent = manifest.get('agent', 'aster')
    RUN_SUFFIX = name
    from docker.models import containers
    install_tarpit_hook(containers)
    public, root = configure(name)
    drv = engine.driver()
    tasks = [t for t in load(root / 'tasks.json')
             if not (root / 'runs' / (agent + '-' + t['id']) / 'launch.json').exists()]
    if not tasks:
        print('All tasks already ran; no model requests made.')
        return
    os.chdir(root)  # SWE-bench writes logs relative to the controller's working directory.
    client = docker.from_env()
    for task in tasks:
        client.images.get(task['image_id'])
    socket = root / 'relay/model.sock'
    assert not socket.exists(), 'Stale gateway socket; inspect before running'
    network = client.networks.create(engine.NETWORK, internal=True)
    process = relay = None
    try:
        with (root / 'gateway.log').open('ab') as log:
            process = subprocess.Popen([PYTHON, str(HERE / 'evaluate.py'), 'gateway', name], cwd=root,
                                       env={**os.environ, 'PYTHONPATH': str(root / 'runtime')},
                                       stdout=log, stderr=log)
        for _ in range(100):
            if socket.exists():
                break
            assert process.poll() is None, 'Gateway exited'
            time.sleep(.1)
        assert socket.exists()
        # Unique container name per run, same DNS alias, so runs can share the host.
        relay = client.containers.run(tasks[0]['image_id'], [PYTHON, '/relay.py'], detach=True,
            name='aster-bench-gateway-' + name, network=engine.NETWORK,
            networking_config={engine.NETWORK: client.api.create_endpoint_config(aliases=['aster-bench-gateway'])},
            volumes={str(root / 'relay'): {'bind': '/relay', 'mode': 'rw'},
                     str(engine.baseline.ORIGINAL / 'relay.py'): {'bind': '/relay.py', 'mode': 'ro'},
                     str(Path(PYTHON).parent.parent): {'bind': str(Path(PYTHON).parent.parent), 'mode': 'ro'}})

        def one(task):
            row = drv.episode(agent, task)
            if task['kind'] == 'repository':
                patch = (root / 'runs' / row['job'] / 'prediction.patch').read_text()
                tarpit_grade(task, agent, patch, f'{HERE.name}-{name}', skip_patch=not patch)
            return row

        with concurrent.futures.ThreadPoolExecutor(max_workers=manifest.get('concurrency', 6)) as pool:
            for future in concurrent.futures.as_completed([pool.submit(one, t) for t in tasks]):
                row = future.result()
                print('finished', row['task'], flush=True)
        verify(name)
    finally:
        if relay is not None:
            relay.remove(force=True)
        if process is not None:
            process.terminate()
            process.wait(timeout=10)
        socket.unlink(missing_ok=True)
        network.remove()
        client.close()


TARPIT_NETWORK = 'aster-grade-tarpit'
# Grading threads run concurrently with episode threads: patch container creation once and
# select the tarpit network per thread, instead of swapping a process-global method.
_TARPIT = threading.local()
_TARPIT_LOCK = threading.Lock()
RUN_SUFFIX = ''


def install_tarpit_hook(containers):
    with _TARPIT_LOCK:
        collection = containers.ContainerCollection
        if getattr(collection.create, '_aster_tarpit', False):
            return
        original = collection.create

        def create(self, *args, **kwargs):
            if getattr(_TARPIT, 'active', False):
                kwargs.setdefault('network', TARPIT_NETWORK)
            # Episode containers are named per job; two runs of one agent share job names.
            if RUN_SUFFIX and str(kwargs.get('name', '')).startswith('aster-general-'):
                kwargs['name'] += '-' + RUN_SUFFIX
            return original(self, *args, **kwargs)

        create._aster_tarpit = True
        collection.create = create


def tarpit_grade(task, agent, patch, run_id, skip_patch=False):
    """Grade on a bridge whose subnet holds 10.255.255.1 with no host behind it.

    requests' TestTimeout cases connect to 10.255.255.1 and need it to blackhole. On the
    default bridge that depends on the host network (on 2026-10-17 the campus network
    answered it). An on-link address with no host never answers ARP, so connects time out
    regardless of the host, while outbound access stays as on the default bridge.
    """
    import docker
    import docker.models.containers as containers

    client = docker.from_env()
    try:
        with _TARPIT_LOCK:
            try:
                client.networks.get(TARPIT_NETWORK)
            except docker.errors.NotFound:
                # Containers take addresses from .128/25, so .1 is never a container (not even the grader).
                pool = docker.types.IPAMPool(subnet='10.255.255.0/24', iprange='10.255.255.128/25',
                                             gateway='10.255.255.254')
                client.networks.create(TARPIT_NETWORK, driver='bridge',
                                       ipam=docker.types.IPAMConfig(pool_configs=[pool]))
    finally:
        client.close()
    install_tarpit_hook(containers)
    _TARPIT.active = True
    try:
        return engine.repository_grade(task, agent, patch, run_id, skip_patch=skip_patch)
    finally:
        _TARPIT.active = False


def regrade(name, sources, only=None):
    """Regrade saved patches on the tarpit network; sources: run name or 'reference', 'pi', 'codex'."""
    public, root = configure(name)
    os.chdir(root)
    rows = {r['instance_id']: r for r in load(root / 'repository-dataset.json')}
    tasks = [t for t in load(root / 'tasks.json') if t['kind'] == 'repository' and (not only or t['id'] in only)]
    previous = public / 'tarpit-regrade.json'
    out = load(previous) if previous.exists() else {}
    for source in sources:
        for task in tasks:
            if source == 'reference':
                patch = rows[task['id']]['patch']
            elif source in ('pi', 'codex', 'aster-c6b651d'):
                agent = source.split('-')[0]
                path = BASE / 'runs' / f"{agent}-{task['id']}" / 'prediction.patch'
                if not path.exists():
                    continue
                patch = path.read_text()
            else:
                patch = (root / 'runs' / ('aster-' + task['id']) / 'prediction.patch').read_text()
            run_id = f'{HERE.name}-{name}-tarpit'
            tarpit_grade(task, source, patch, run_id, skip_patch=not patch)
            report = root / 'logs/evaluation' / run_id / source / task['id'] / 'report.json'
            grade = load(report).get(task['id']) if report.exists() else None
            out.setdefault(source, {})[task['id']] = bool(grade and grade.get('resolved'))
            print(source, task['id'], out[source][task['id']], flush=True)
    save(public / 'tarpit-regrade.json', out)


def model_seconds(out):
    total = 0.0
    for path in (out / 'provider').glob('*.json'):
        if not path.name.endswith('.request.json'):
            total += load(path).get('seconds', 0)
    return total


def weighted(row):
    cached = row['cached_tokens']
    return ((row['input_tokens'] - cached) * 1.25 + cached * 0.125 + row['output_tokens'] * 10) / 1e6


def results(name):
    agent = verify(name).get('agent', 'aster')
    public, root = configure(name)
    helpers = imported('harness_v12_summary', HERE.parent / 'general-coding-20261010/summarize.py')
    rows = []
    for task in load(root / 'tasks.json'):
        out = root / 'runs' / (agent + '-' + task['id'])
        if not (out / 'result.json').exists():
            continue
        row = load(out / 'result.json')
        usage, _ = helpers.usage(out)
        final, phase, stop = helpers.final_text(out, agent)
        row.update(usage, final=final, phase=phase, stop_reason=stop, exposure=task['exposure'], kind=task['kind'])
        if task['kind'] == 'repository':
            path = root / 'logs/evaluation' / f'{HERE.name}-{name}' / agent / task['id'] / 'report.json'
            regraded = root / 'logs/evaluation' / f'{HERE.name}-{name}-tarpit' / name / task['id'] / 'report.json'
            if regraded.exists():  # runs graded before tarpit_grade existed were regraded on it
                path = regraded
                row['grading_network'] = TARPIT_NETWORK
            grade = load(path).get(task['id']) if path.exists() else None
            row.update(grade=grade, graded=grade is not None, passed=bool(grade and grade.get('resolved')))
        else:
            row.update(graded='grade' in row, passed=bool(row.get('grade', {}).get('passed')))
        # Same definition as general-50: phase only exists for Aster.
        row['normal_delivery_and_passed'] = bool(row['passed'] and row['exit_code'] == 0 and final
                                                 and (agent != 'aster' or phase == 'completed'))
        row['model_seconds'] = round(model_seconds(out), 3)
        row['weighted_cost'] = round(weighted(row), 6)
        rows.append(row)
    save(public / 'results.json', rows)
    save(public / 'metrics.json', measure(root / 'runs'))
    try:
        turns = measure_turns(root / 'runs')
    except (ValueError, KeyError, TypeError) as exc:  # chat transcripts lack Responses items
        turns = {'unavailable': f'{type(exc).__name__}: {exc}'}
    save(public / 'turns.json', turns)
    total = {k: sum(r[k] for r in rows) for k in ('requests', 'input_tokens', 'cached_tokens', 'output_tokens',
                                                    'seconds', 'model_seconds', 'weighted_cost', 'passed',
                                                    'normal_delivery_and_passed', 'missing_usage')}
    total['harness_seconds'] = round(total['seconds'] - total['model_seconds'], 1)
    total['finished'], total['graded'] = len(rows), sum(r['graded'] for r in rows)
    save(public / 'summary.json', total)
    print(json.dumps(total))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['prepare', 'verify', 'run', 'gateway', 'results', 'regrade'])
    parser.add_argument('--sources', nargs='+', default=['run'], help='regrade: run, reference, pi, codex')
    parser.add_argument('name')
    parser.add_argument('--ref', help='git ref to export (prepare)')
    parser.add_argument('--tasks', nargs='+', default=['old20'], help='old20, new30, a pool tag (fresh30, fresh30b), or task ids (prepare)')
    parser.add_argument('--pool', default='general-coding-50-20261012', help='private task pool directory (prepare)')
    parser.add_argument('--agent', default='aster', choices=['aster', 'pi', 'codex'], help='agent to run (prepare)')
    parser.add_argument('--model', default='gpt-5.6-luna', help='model the gateway requests (prepare)')
    parser.add_argument('--protocol', default='responses', choices=['responses', 'chat'],
                        help='protocol Aster speaks to the gateway (prepare)')
    parser.add_argument('--key-env', default='ASTER_API_KEY', help='.env variable holding the key (prepare)')
    parser.add_argument('--base-url-env', default='ASTER_BASE_URL',
                        help='.env variable holding the provider base URL (prepare)')
    parser.add_argument('--concurrency', type=int, default=6,
                        help='episodes at once (prepare); keep the host total at 6 so timings stay comparable')
    args = parser.parse_args()
    if args.mode == 'prepare':
        prepare(args.name, args.ref, args.tasks, args.pool, args.agent, args.concurrency, args.model,
                args.protocol, args.key_env, args.base_url_env)
    elif args.mode == 'verify':
        verify(args.name)
        print('Frozen source verified; no model requests made.')
    elif args.mode == 'gateway':
        verify(args.name)
        configure(args.name)
        serve_gateway(args.name)
    elif args.mode == 'run':
        run(args.name)
    elif args.mode == 'regrade':
        only = None if args.tasks in (['old20'], ['new30']) else args.tasks
        regrade(args.name, [args.name if source == 'run' else source for source in args.sources], only)
    else:
        results(args.name)


if __name__ == '__main__':
    main()
