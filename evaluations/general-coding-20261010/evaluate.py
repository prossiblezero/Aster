"""Isolated three-agent evaluation, reusing the existing model gateway and SWE scorer."""
import argparse
import concurrent.futures
import hashlib
import importlib.util
import json
import os
import shlex
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
PRIVATE = REPO / 'artifacts/private' / HERE.name
ORIGINAL = HERE.parent / 'swebench-compare-20261008'
NETWORK = 'aster-general-20261010'
AGENTS = ('aster', 'pi', 'codex')
CONTROLLER = REPO / 'artifacts/private/swebench-compare-20261008/controller-env/bin'
CODEX = Path('/usr/local/lib/node_modules/@openai/codex/node_modules/@openai/codex-linux-x64/vendor/x86_64-unknown-linux-musl')


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def load(path):
    return json.loads(path.read_text())


def module(name):
    spec = importlib.util.spec_from_file_location('general_' + name, ORIGINAL / (name + '.py'))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    result.PRIVATE = PRIVATE
    return result


def hashes(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob('*')) if p.is_file() and '__pycache__' not in p.parts}


def verify_frozen():
    manifest = load(HERE / 'manifest.json')
    assert hashes(PRIVATE / 'runtime') == manifest['runtime_hashes']
    assert hashes(REPO / 'src/aster') == manifest['product_hashes']
    for name, digest in manifest['runner_hashes'].items():
        assert hashlib.sha256((REPO / name).read_bytes()).hexdigest() == digest, name
    assert hashlib.sha256((PRIVATE / 'tasks.json').read_bytes()).hexdigest() == manifest['tasks_sha256']
    for key, path in {'pi_cli': PRIVATE / 'pi/node_modules/@earendil-works/pi-coding-agent/dist/bundle/cli.js',
                      'codex_binary': CODEX / 'bin/codex'}.items():
        assert hashlib.sha256(path.read_bytes()).hexdigest() == manifest['agent_binary_hashes'][key]
    return manifest


def environment(task, job):
    return {'PATH': str(Path(task['test_python']).parent) + ':/opt/node/bin:/opt/codex-runtime/codex-path:/usr/local/bin:/usr/bin:/bin',
            'PYTHONPATH': task['workspace'], 'BENCH_WORKSPACE': task['workspace'],
            'BENCH_TEST_PYTHON': task['test_python'],
            'BENCH_BASE_URL': f'http://aster-bench-gateway:18080/{job}/v1',
            'BENCH_API_KEY': 'benchmark', 'PI_CODING_AGENT_DIR': '/agent-state/pi',
            'PI_OFFLINE': '1', 'TIKTOKEN_CACHE_DIR': '/opt/agent-code/tiktoken-cache',
            'OPENBLAS_NUM_THREADS': '1', 'OMP_NUM_THREADS': '1', 'TERM': 'dumb'}


def start_container(task, job):
    import docker
    helper = module('compare')
    client = docker.from_env(timeout=1000)
    volumes = {str(PRIVATE / 'runtime'): {'bind': '/opt/agent-code', 'mode': 'ro'},
               '/home/dzh/.conda/envs/aster': {'bind': '/home/dzh/.conda/envs/aster', 'mode': 'ro'},
               str((PRIVATE / 'task-python').resolve()): {'bind': '/opt/task-python', 'mode': 'ro'},
               str((PRIVATE / 'pi').resolve()): {'bind': '/opt/pi', 'mode': 'ro'},
               str((PRIVATE / 'node-v22.22.0-linux-x64').resolve()): {'bind': '/opt/node', 'mode': 'ro'},
               str(CODEX): {'bind': '/opt/codex-runtime', 'mode': 'ro'},
               str(Path('/usr/lib/x86_64-linux-gnu/libseccomp.so.2').resolve()):
                   {'bind': '/usr/lib/x86_64-linux-gnu/libseccomp.so.2', 'mode': 'ro'}}
    container = client.containers.run(task['image_id'], 'tail -f /dev/null', detach=True,
                                     name='aster-general-' + job, network=NETWORK,
                                     volumes=volumes, working_dir=task['workspace'], user='root',
                                     nano_cpus=4_000_000_000, mem_limit='8g', pids_limit=512)
    try:
        helper.checked(container, ['mkdir', '-p', '/agent-state/pi', '/root/.codex'])
        if task['kind'] == 'repository':
            helper.checked(container, ['bash', '-c', 'git reset --hard ' + shlex.quote(task['base_commit'])
                + ' && git clean -fd && rm -rf .git && git init -q && git add .'
                  ' && git -c user.name=Benchmark -c user.email=benchmark@localhost commit -qm base'])
        elif task['kind'] == 'small':
            folder = PRIVATE / 'sources/aider-tasks' / task['task_name']
            for path in folder.glob('*.py'):
                helper.put(container, '/app/' + path.name, path.read_bytes())
        helper.checked(container, [task['test_python'], '-c', 'import sys; print(sys.version)'])
        return client, container
    except BaseException:
        container.remove(force=True)
        client.close()
        raise


def grade_local(task, container, output):
    """Inject immutable tests only after the agent process has stopped."""
    helper = module('compare')
    helper.checked(container, ['mkdir', '-p', '/tests', '/logs/verifier'])
    if task['kind'] == 'small':
        folder = PRIVATE / 'sources/aider-tasks' / task['task_name']
        tests = list(folder.glob('*_test.py'))
        assert len(tests) == 1
        helper.put(container, '/app/' + tests[0].name, tests[0].read_bytes())
        target = '/app/' + tests[0].name
    else:
        folder = PRIVATE / 'sources/terminal-tasks' / task['task_name'] / 'tests'
        for path in folder.rglob('*'):
            if path.is_file():
                dest = '/tests/' + str(path.relative_to(folder))
                helper.checked(container, ['mkdir', '-p', str(Path(dest).parent)])
                helper.put(container, dest, path.read_bytes())
        if task['task_name'] == 'cancel-async-tasks':
            helper.checked(container, ['cp', '/tests/test.py', '/app/test.py'])
        target = '/tests/test_outputs.py'
    env = environment(task, 'grading')
    result = container.exec_run(['timeout', '--kill-after=10', '900', task['test_python'], '-m',
                                'pytest', '-rA', '-p', 'no:cacheprovider',
                                '--junitxml=/logs/verifier/junit.xml', target],
                               workdir=task['workspace'], environment=env)
    output.mkdir(parents=True, exist_ok=True)
    (output / 'test_output.txt').write_bytes(result.output)
    xml = container.exec_run(['cat', '/logs/verifier/junit.xml'])
    counts = dict(tests=0, failures=0, errors=0, skipped=0)
    if xml.exit_code == 0:
        (output / 'junit.xml').write_bytes(xml.output)
        tree = ET.fromstring(xml.output)
        for suite in tree.iter('testsuite'):
            for key in counts:
                counts[key] += int(suite.attrib.get(key, 0))
    grade = {'passed': result.exit_code == 0 and counts['tests'] > 0 and
             not (counts['errors'] or counts['failures'] or counts['skipped']),
             'exit_code': result.exit_code, **counts,
             'mode': 'unchanged_upstream_assertions_python312_in_native_task_image'}
    reference = PRIVATE / 'preflight' / task['id'] / 'preflight.json'
    if reference.exists():
        grade['expected_tests'] = load(reference)['reference']['tests']
        grade['passed'] &= counts['tests'] == grade['expected_tests']
    save(output / 'grade.json', grade)
    return grade


def preflight_local(task):
    helper = module('compare')
    out = PRIVATE / 'preflight' / task['id']
    if (out / 'preflight.json').exists():
        return load(out / 'preflight.json')
    grades = {}
    for variant in ('starter', 'reference'):
        client, container = start_container(task, 'preflight-' + variant + '-' + task['id'])
        try:
            if variant == 'reference':
                if task['kind'] == 'small':
                    folder = PRIVATE / 'sources/aider-tasks' / task['task_name']
                    starter = [p for p in folder.glob('*.py') if not p.name.endswith('_test.py')]
                    assert len(starter) == 1
                    helper.put(container, '/app/' + starter[0].name, (folder / '.meta/example.py').read_bytes())
                else:
                    oracle = (PRIVATE / 'sources/terminal-tasks' / task['task_name'] / 'solution/solve.sh').read_text()
                    # gRPC dependencies are preinstalled offline for all three agents.
                    oracle = oracle.replace('pip install grpcio==1.73.0 grpcio-tools==1.73.0', ':')
                    helper.put(container, '/agent-state/reference.sh', oracle)
                    result = container.exec_run(['timeout', '--kill-after=10', '900', 'bash', '/agent-state/reference.sh'],
                                                environment=environment(task, 'reference'), workdir='/app')
                    out.mkdir(parents=True, exist_ok=True)
                    (out / 'reference.log').write_bytes(result.output)
                    assert result.exit_code == 0, ('reference_execution_failed', task['id'])
            grades[variant] = grade_local(task, container, out / variant)
        finally:
            container.remove(force=True)
            client.close()
    result = {'id': task['id'], 'ready': not grades['starter']['passed'] and grades['reference']['passed'], **grades}
    save(out / 'preflight.json', result)
    print(json.dumps({'preflight': task['id'], 'ready': result['ready']}), flush=True)
    return result


def episode(agent, task, smoke=False):
    manifest = verify_frozen()
    helper = module('compare')
    job = f'{agent}-{task["id"]}' if not smoke else f'smoke-{agent}'
    out = PRIVATE / 'runs' / job
    if (out / 'result.json').exists():
        return load(out / 'result.json')
    assert not (out / 'launch.json').exists(), 'Classify incomplete episode before proceeding: ' + job
    prompt = task['prompt'] + ('\n\nWork in ' + task['workspace'] + '. Dependencies are preinstalled. '
        'python on PATH is the task interpreter. Internet access is unavailable; local services are allowed. '
        'Implement the request, run relevant checks, and summarize the actual result.')
    if task['kind'] == 'repository':
        command = {'django/django': 'python tests/runtests.py --settings=test_sqlite --parallel 1 <test label>',
                   'sympy/sympy': 'python bin/test -C --verbose <test path>'}.get(task['repo'], 'python -m pytest <test path>')
        prompt += ' Public test command format: ' + command + '.'
    if smoke:
        prompt = ('In /testbed create evaluation_smoke.py with add(a,b) returning a+b, and '
                  'test_evaluation_smoke.py containing one unittest asserting add(2,3)==5. '
                  'Run python -m unittest test_evaluation_smoke, then finish.')
    env = environment(task, job)
    launch = {'job': job, 'agent': agent, 'task': task['id'], 'smoke': smoke, 'prompt': prompt,
              'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest(), 'image_id': task['image_id'],
              'model': manifest['model'], 'seconds_limit': 240 if smoke else 900,
              'source_commit': manifest['aster_commit'], 'started': time.time(),
              'runner_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    save(out / 'launch.json', launch)
    client, container = start_container(task, job)
    try:
        helper.put(container, '/agent-state/prompt.txt', prompt)
        helper.put(container, '/agent-state/pi/models.json', json.dumps({'providers': {'benchmark': {
            'baseUrl': env['BENCH_BASE_URL'], 'api': 'openai-responses', 'apiKey': 'benchmark',
            'models': [{'id': manifest['model'], 'name': manifest['model'], 'reasoning': True,
                        'input': ['text'], 'contextWindow': 64000, 'maxTokens': 8192,
                        'cost': {'input': 0, 'output': 0, 'cacheRead': 0, 'cacheWrite': 0}}]}}}))
        helper.put(container, '/agent-state/pi/settings.json', json.dumps({'transport': 'sse', 'cacheWarming': 'off'}))
        helper.put(container, '/root/.codex/config.toml', '\n'.join([
            'model = "gpt-5.6-luna"', 'model_provider = "benchmark"', 'model_reasoning_effort = "medium"',
            'model_context_window = 64000', 'model_auto_compact_token_limit = 54000',
            'web_search = "disabled"', 'approval_policy = "never"', 'sandbox_mode = "danger-full-access"',
            '[features]', 'multi_agent = false', '[model_providers.benchmark]', 'name = "benchmark"',
            f'base_url = "{env["BENCH_BASE_URL"]}"', 'env_key = "BENCH_API_KEY"', 'wire_api = "responses"']) + '\n')
        if agent == 'aster':
            command = ['/home/dzh/.conda/envs/aster/bin/python', '/opt/agent-code/aster_episode.py']
            env['PYTHONPATH'] = '/opt/agent-code'
        elif agent == 'pi':
            command = ['/opt/node/bin/node', '/opt/pi/node_modules/@earendil-works/pi-coding-agent/dist/bundle/cli.js',
                       '--mode', 'json', '--provider', 'benchmark', '--model', manifest['model'], '--thinking', 'medium',
                       '--no-session', '--no-extensions', '--no-skills', '--no-prompt-templates', '--offline', prompt]
        else:
            command = ['/opt/codex-runtime/bin/codex', 'exec', '--json', '--ephemeral', '--ignore-rules',
                       '--skip-git-repo-check', '--dangerously-bypass-approvals-and-sandbox', '-C', task['workspace'], prompt]
        argv = ['docker', 'exec']
        for key, value in env.items():
            argv += ['--env', f'{key}={value}']
        argv += [container.id, 'timeout', '--signal=TERM', '--kill-after=10', str(launch['seconds_limit']), *command]
        print(json.dumps({'started': job}), flush=True)
        started = time.monotonic()
        with (out / 'agent.jsonl').open('wb') as stdout, (out / 'stderr.log').open('wb') as stderr:
            process = subprocess.Popen(argv, stdout=stdout, stderr=stderr)
            try:
                code = process.wait(timeout=launch['seconds_limit'] + 30)
            except subprocess.TimeoutExpired:
                container.kill()
                process.kill()
                code = process.wait()
                container.start()
        result = {'job': job, 'agent': agent, 'task': task['id'], 'smoke': smoke,
                  'exit_code': code, 'seconds': round(time.monotonic() - started, 3),
                  'timed_out': code in {124, 137}, 'kind': task['kind'],
                  'previously_used': task['previously_used']}
        if agent == 'aster':
            state = container.exec_run(['cat', '/agent-state/aster-result.json'])
            if state.exit_code == 0:
                (out / 'aster-result.json').write_bytes(state.output)
        if task['kind'] == 'repository':
            helper.checked(container, ['bash', '-c', "git add -A -- . ':!.aster' ':!.tmp' ':!.pi' ':!.codex'"])
            patch = helper.checked(container, ['git', 'diff', '--cached', '--binary', 'HEAD'])
            (out / 'prediction.patch').write_bytes(patch)
            result.update(patch_bytes=len(patch), patch_sha256=hashlib.sha256(patch).hexdigest())
        for name in (('/agent-state', task['workspace'] + '/.aster') if agent == 'aster' else ('/agent-state',)):
            try:
                stream, _ = container.get_archive(name)
                with (out / (Path(name).name + '.tar')).open('wb') as target:
                    for chunk in stream:
                        target.write(chunk)
            except Exception as exc:
                result.setdefault('archive_errors', []).append(type(exc).__name__)
        if smoke:
            test = container.exec_run([task['test_python'], '-m', 'unittest', 'test_evaluation_smoke'], workdir='/testbed')
            (out / 'smoke-test.log').write_bytes(test.output)
            result['smoke_passed'] = code == 0 and test.exit_code == 0
            if agent == 'aster':
                result['smoke_passed'] &= (out / 'aster-result.json').exists() and load(out / 'aster-result.json')['phase'] == 'completed'
        elif task['kind'] != 'repository':
            stream, _ = container.get_archive(task['workspace'])
            with (out / 'candidate.tar').open('wb') as target:
                for chunk in stream:
                    target.write(chunk)
            result['grade'] = grade_local(task, container, out / 'grading')
        save(out / 'result.json', result)
        print(json.dumps(result), flush=True)
        return result
    finally:
        container.remove(force=True)
        client.close()


def freeze(tasks):
    assert not any((PRIVATE / 'runs').glob('*/launch.json'))
    paths = [HERE / name for name in ('evaluate.py', 'aster_episode.py', 'PROTOCOL.md', 'prepare.py')]
    paths += [ORIGINAL / name for name in ('gateway.py', 'compare.py', 'execution_environment.py', 'relay.py')]
    manifest = {'aster_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=REPO).decode().strip(),
                'pi_version': '1.1.0', 'codex_version': '0.154.0', 'model': 'gpt-5.6-luna',
                'concurrency': 3, 'seconds_per_task': 900, 'context_tokens': 64000, 'output_tokens': 8192,
                'tasks': [t['id'] for t in tasks], 'planned_episodes': len(tasks) * 3,
                'tasks_sha256': hashlib.sha256((PRIVATE / 'tasks.json').read_bytes()).hexdigest(),
                'product_hashes': hashes(REPO / 'src/aster'), 'runtime_hashes': hashes(PRIVATE / 'runtime'),
                'runner_hashes': {str(p.relative_to(REPO)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},
                'source_hashes': hashes(PRIVATE / 'sources/terminal-tasks') | {'aider/' + k: v for k, v in hashes(PRIVATE / 'sources/aider-tasks').items()}}
    save(HERE / 'manifest.json', manifest)
    print('Evaluation frozen:', manifest['planned_episodes'], 'formal episodes')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('gateway', 'freeze', 'preflight', 'smoke', 'run', 'export'))
    parser.add_argument('--task')
    parser.add_argument('--agent', choices=AGENTS)
    args = parser.parse_args()
    if args.mode == 'gateway':
        from socketserver import ThreadingMixIn, UnixStreamServer
        gateway = module('gateway')
        assert gateway.SETTINGS.model == 'gpt-5.6-luna'
        class Server(ThreadingMixIn, UnixStreamServer):
            daemon_threads = True
        with Server(str(PRIVATE / 'relay/model.sock'), gateway.Gateway) as server:
            os.chmod(PRIVATE / 'relay/model.sock', 0o666)
            print('General benchmark gateway ready', flush=True)
            server.serve_forever()
        return
    tasks = load(PRIVATE / 'tasks.json')
    if args.mode == 'freeze':
        freeze(tasks)
    elif args.mode == 'preflight':
        for task in tasks:
            if task['kind'] != 'repository' and (not args.task or task['id'] == args.task):
                assert preflight_local(task)['ready'], task['id']
    elif args.mode == 'smoke':
        for agent in ((args.agent,) if args.agent else AGENTS):
            assert episode(agent, tasks[0], smoke=True)['smoke_passed'], agent
    elif args.mode == 'run':
        verify_frozen()
        for agent in AGENTS:
            assert load(PRIVATE / 'runs' / ('smoke-' + agent) / 'result.json')['smoke_passed']
        assert load(PRIVATE / 'preflight/summary.json')['ready']
        jobs = []
        for index, task in enumerate(tasks):
            if args.task and task['id'] != args.task:
                continue
            for offset in range(3):
                agent = AGENTS[(index + offset) % 3]
                if not args.agent or agent == args.agent:
                    jobs.append((agent, task))
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            futures = [pool.submit(episode, agent, task) for agent, task in jobs]
            for future in concurrent.futures.as_completed(futures):
                future.result()
    else:
        for agent in AGENTS:
            predictions = []
            for task in tasks:
                if task['kind'] != 'repository':
                    continue
                out = PRIVATE / 'runs' / (agent + '-' + task['id'])
                if (out / 'result.json').exists():
                    predictions.append({'instance_id': task['id'], 'model_name_or_path': agent,
                                        'model_patch': (out / 'prediction.patch').read_text()})
            path = PRIVATE / (agent + '.predictions.jsonl')
            path.write_text(''.join(json.dumps(row) + '\n' for row in predictions))
            print(agent, len(predictions), 'repository predictions')


if __name__ == '__main__':
    main()
