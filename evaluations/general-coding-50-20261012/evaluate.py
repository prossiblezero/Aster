"""50-task comparison using the existing three-agent runner and official SWE scorer."""
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
from socketserver import ThreadingMixIn, UnixStreamServer

from prepare import DATA, HERE, PRIVATE, REPO, load, save

PYTHON = '/home/dzh/.conda/envs/aster/bin/python'
NETWORK = 'aster-general50-20261012'
AGENTS = ('aster', 'pi', 'codex')
CONCURRENCY = 6
IMPORTS = {'django/django': 'django', 'sympy/sympy': 'sympy', 'sphinx-doc/sphinx': 'sphinx',
           'pydata/xarray': 'xarray', 'pytest-dev/pytest': 'pytest', 'psf/requests': 'requests',
           'astropy/astropy': 'astropy.table', 'scikit-learn/scikit-learn': 'sklearn.tree',
           'matplotlib/matplotlib': 'matplotlib.pyplot', 'pylint-dev/pylint': 'pylint'}


def imported(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


baseline = imported('general50_base', HERE.parent / 'general-coding-20261010/evaluate.py')
hashes = baseline.hashes


def verify_frozen():
    manifest = load(HERE / 'manifest.json')
    assert hashes(PRIVATE / 'runtime/aster') == manifest['product_hashes']
    assert hashes(PRIVATE / 'runtime') == manifest['runtime_hashes']
    for name, expected in manifest['external_binary_hashes'].items():
        assert hashlib.sha256(Path(name).read_bytes()).hexdigest() == expected, name
    for name, expected in manifest['input_hashes'].items():
        assert hashlib.sha256((REPO / name).read_bytes()).hexdigest() == expected, name
    return manifest


def driver():
    drv = imported('general50_driver', HERE.parent / 'general-coding-20261010/evaluate.py')
    drv.PRIVATE, drv.NETWORK, drv.verify_frozen = PRIVATE, NETWORK, verify_frozen
    original_environment = drv.environment
    original_module = drv.module
    original_start = drv.start_container
    original_grade = drv.grade_local
    original_episode = drv.episode

    def helper_module(name):
        helper = original_module(name)
        if name == 'compare':
            checked = helper.checked

            def preserving_checked(container, command, **kwargs):
                marker = ' && rm -rf .git && git init -q && git add .'
                if command[:2] == ['bash', '-c'] and marker in command[2]:
                    command = [*command[:2], command[2].replace(marker,
                        ' && git ls-files -z > /agent-state/original-index.z'
                        ' && rm -rf .git && git init -q && git add .'
                        ' && xargs -0 -r git add -f -- < /agent-state/original-index.z')]
                return checked(container, command, **kwargs)
            helper.checked = preserving_checked
        return helper

    def environment(task, job):
        env = original_environment(task, job)
        env.update(MPLBACKEND='Agg',
                   NODE_PATH='/opt/task-python/js/node_modules', BENCH_LANGUAGE=task['language'])
        return env

    def copy_file(helper, container, source, target):
        helper.checked(container, ['mkdir', '-p', str(Path(target).parent)])
        contents = source.read_bytes()
        if source.name.endswith('.spec.js'):
            # Exercism disables mandatory follow-up cases for incremental practice.
            # Keep explicitly optional test.skip cases as upstream, enable xtest/xit.
            contents = contents.replace(b'xtest(', b'test(').replace(b'xit(', b'it(')
        helper.put(container, target, contents)

    def start(task, job):
        client, container = original_start(task, job)
        helper = drv.module('compare')
        try:
            env = environment(task, job)
            # Login shells used by Codex must resolve the same interpreter as other tools.
            helper.put(container, '/etc/profile.d/benchmark-path.sh', 'export PATH=' + shlex.quote(env['PATH']) + '\n')
            helper.checked(container, ['bash', '-c',
                'for profile in /root/.bash_profile /root/.bash_login /root/.profile; do '
                'if [ -f "$profile" ]; then printf "\\n. /etc/profile.d/benchmark-path.sh\\n" >> "$profile"; break; fi; done'])
            if task['kind'] in ('small', 'library') and task['exposure'] == 'new30':
                helper.checked(container, ['bash', '-c', 'find /app -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +'])
                if task['kind'] == 'library':
                    folder = PRIVATE / 'sources/library-tasks' / task['task_name']
                    copy_file(helper, container, folder / 'solution.py', '/app/solution.py')
                else:
                    folder = PRIVATE / 'sources/aider-tasks' / task['task_name']
                    for path in folder.rglob('*'):
                        relative = path.relative_to(folder)
                        if path.is_file() and not any(p.startswith('.') for p in relative.parts):
                            copy_file(helper, container, path, '/app/' + str(relative))
                    if task['language'] == 'JavaScript':
                        helper.checked(container, ['ln', '-s', '/opt/task-python/js/node_modules', '/app/node_modules'])
                        helper.checked(container, ['mkdir', '-p', '/usr/local/bin'])
                        helper.checked(container, ['ln', '-sf', '/opt/node/bin/node', '/usr/local/bin/node'])
                    elif task['language'] == 'C++':
                        # Upstream CMake derives the exercise name from the directory name.
                        cmake = (folder / 'CMakeLists.txt').read_text().replace(
                            'get_filename_component(exercise ${CMAKE_CURRENT_SOURCE_DIR} NAME)',
                            'set(exercise ' + task['task_name'].removeprefix('cpp-') + ')')
                        helper.put(container, '/app/CMakeLists.txt', cmake)
            layout = {'pytest-dev/pytest': 'src', 'matplotlib/matplotlib': 'lib'}.get(task.get('repo'))
            if layout:
                site = helper.checked(container, [task['test_python'], '-c',
                    'import site; print(site.getsitepackages()[0])']).decode().strip()
                helper.put(container, site + '/benchmark_workspace.pth',
                    'import os, sys; sys.path.insert(0, os.path.join(os.getcwd(), ' + repr(layout) + '))\n')
            if task.get('repo') == 'pytest-dev/pytest':
                helper.checked(container, ['git', 'add', '-f', 'src/_pytest/_version.py'])
                helper.checked(container, ['git', '-c', 'user.name=Benchmark', '-c', 'user.email=benchmark@localhost',
                                           'commit', '--amend', '--no-edit', '-q'])
            if task.get('repo') in ('astropy/astropy', 'scikit-learn/scikit-learn', 'matplotlib/matplotlib'):
                # Keep prebuilt extension modules available in copied workspaces.
                helper.checked(container, ['bash', '-c',
                    "find . -name '*.so' -type f -print0 | xargs -0 -r git add -f --"])
                if task.get('repo') == 'matplotlib/matplotlib':
                    helper.checked(container, ['git', 'add', '-f', 'lib/matplotlib/_version.py'])
                helper.checked(container, ['git', '-c', 'user.name=Benchmark', '-c', 'user.email=benchmark@localhost',
                                           'commit', '--amend', '--no-edit', '-q'])
            preload = IMPORTS.get(task.get('repo'))
            if task['kind'] == 'library':
                preload = 'numpy, pandas, matplotlib.pyplot, scipy, sklearn'
            if preload:
                helper.checked(container, ['timeout', '180', task['test_python'], '-c', 'import ' + preload],
                               environment=env)
            return client, container
        except BaseException:
            container.remove(force=True)
            client.close()
            raise

    def grade(task, container, output):
        if task['kind'] == 'terminal' or task['exposure'] == 'old20':
            return original_grade(task, container, output)
        helper = drv.module('compare')
        helper.checked(container, ['mkdir', '-p', '/logs/verifier'])
        output.mkdir(parents=True, exist_ok=True)
        if task['kind'] == 'library':
            folder = PRIVATE / 'sources/library-tasks' / task['task_name']
            # BigCodeBench executes candidate and tests in the same global namespace.
            # Importing only task_func loses imports referenced by upstream tests.
            candidate = helper.checked(container, ['cat', '/app/solution.py'])
            helper.put(container, '/app/test_solution.py', candidate + b'\n\n' + load(folder / 'record.json')['test'].encode())
        else:
            folder = PRIVATE / 'sources/aider-tasks' / task['task_name']
            for name in task['files']['test']:
                copy_file(helper, container, folder / name, '/app/' + name)
        env = environment(task, 'grading')
        xml_path = '/logs/verifier/junit.xml'
        if task['language'] == 'JavaScript':
            command = ['/opt/node/bin/node', '/opt/task-python/js/node_modules/jest/bin/jest.js',
                       '--runInBand', '--no-cache', '--json', '--outputFile=/logs/verifier/jest.json']
        elif task['language'] == 'C++':
            files = [n for n in task['files']['solution'] + task['files']['test'] if n.endswith('.cpp')]
            command = ['bash', '-c', 'g++ -std=c++17 -DEXERCISM_RUN_ALL_TESTS -I. '
                + ' '.join(shlex.quote(n) for n in files) + ' test/tests-main.cpp -lboost_date_time -o /app/test-binary'
                + ' && /app/test-binary --reporter junit --out ' + xml_path]
        else:
            test = 'test_solution.py' if task['kind'] == 'library' else task['files']['test'][0]
            command = [task['test_python'], '-m', 'pytest', '-rA', '-p', 'no:cacheprovider',
                       '--junitxml=' + xml_path, test]
        result = container.exec_run(['timeout', '--kill-after=10', '900', *command], workdir='/app', environment=env)
        (output / 'test_output.txt').write_bytes(result.output)
        counts = dict(tests=0, failures=0, errors=0, skipped=0)
        if task['language'] == 'JavaScript':
            report = container.exec_run(['cat', '/logs/verifier/jest.json'])
            if report.exit_code == 0:
                (output / 'jest.json').write_bytes(report.output)
                data = json.loads(report.output)
                counts.update(tests=data['numTotalTests'], failures=data['numFailedTests'],
                              skipped=data['numPendingTests'], errors=data['numRuntimeErrorTestSuites'])
        else:
            report = container.exec_run(['cat', xml_path])
            if report.exit_code == 0:
                (output / 'junit.xml').write_bytes(report.output)
                for suite in ET.fromstring(report.output).iter('testsuite'):
                    for key in counts:
                        counts[key] += int(suite.attrib.get(key, 0))
        grade = {'passed': result.exit_code == 0 and counts['tests'] > 0 and not (counts['failures'] or counts['errors']),
                 'exit_code': result.exit_code, **counts, 'mode': 'original_assertions_local_adapter'}
        reference = PRIVATE / 'preflight' / task['id'] / 'preflight.json'
        if reference.exists():
            expected = load(reference)['reference']
            grade['expected_tests'], grade['expected_skipped'] = expected['tests'], expected['skipped']
            grade['passed'] &= (grade['tests'], grade['skipped']) == (expected['tests'], expected['skipped'])
        save(output / 'grade.json', grade)
        return grade

    def isolated_grade(task, container, output):
        if task['kind'] == 'terminal':
            return grade(task, container, output)
        client, grader = start(task, 'score-' + task['id'] + '-' + str(time.time_ns()))
        helper = drv.module('compare')
        try:
            if task['kind'] == 'library':
                solutions = ['solution.py']
            else:
                folder = PRIVATE / 'sources/aider-tasks' / task['task_name']
                solutions = (task.get('files') or load(folder / '.meta/config.json')['files'])['solution']
            for name in solutions:
                candidate = container.exec_run(['cat', '/app/' + name])
                if candidate.exit_code == 0:
                    helper.put(grader, '/app/' + name, candidate.output)
                else:
                    helper.checked(grader, ['rm', '-f', '/app/' + name])
            result = grade(task, grader, output)
            result['fresh_grading_container'] = True
            save(output / 'grade.json', result)
            return result
        finally:
            grader.remove(force=True)
            client.close()

    drv.module, drv.environment, drv.start_container, drv.grade_local = helper_module, environment, start, isolated_grade
    drv.grade_control = grade
    drv.copy_file = copy_file

    def episode(agent, task, smoke=False):
        task = dict(task)
        if task['kind'] == 'small':
            if task['language'] == 'C++':
                command = 'cmake -S . -B build -DEXERCISM_RUN_ALL_TESTS=ON && cmake --build build'
            elif task['language'] == 'JavaScript':
                command = '/opt/node/bin/node /opt/task-python/js/node_modules/jest/bin/jest.js --runInBand'
            else:
                command = 'python -m pytest -q'
            task['prompt'] += '\n\nPublic test command: ' + command + '. Implement the provided solution files; the grader restores the original tests.'
        task['prompt'] += '\n\nCommands start in the task workspace. Use workspace-relative paths when possible.'
        return original_episode(agent, task, smoke=smoke)
    drv.episode = episode
    return drv


def local_preflight(drv, task):
    out = PRIVATE / 'preflight' / task['id']
    if (out / 'preflight.json').exists():
        return load(out / 'preflight.json')
    helper = drv.module('compare')
    grades = {}
    for variant in ('starter', 'reference'):
        client, container = drv.start_container(task, 'preflight-' + variant + '-' + task['id'])
        try:
            if variant == 'reference':
                if task['kind'] == 'terminal':
                    folder = PRIVATE / 'sources/terminal-tasks' / task['task_name'] / 'solution'
                    for path in folder.rglob('*'):
                        if path.is_file():
                            drv.copy_file(helper, container, path, '/solution/' + str(path.relative_to(folder)))
                    script = (folder / 'solve.sh').read_text().replace('pip install grpcio==1.73.0 grpcio-tools==1.73.0', ':')
                    helper.put(container, '/solution/solve.sh', script)
                    result = container.exec_run(['timeout', '900', 'bash', '/solution/solve.sh'],
                                                environment=drv.environment(task, 'reference'), workdir='/app')
                    out.mkdir(parents=True, exist_ok=True)
                    (out / 'reference.log').write_bytes(result.output)
                    assert result.exit_code == 0, task['id']
                elif task['kind'] == 'library':
                    folder = PRIVATE / 'sources/library-tasks' / task['task_name']
                    drv.copy_file(helper, container, folder / 'reference.py', '/app/solution.py')
                else:
                    folder = PRIVATE / 'sources/aider-tasks' / task['task_name']
                    config = task.get('files') or load(folder / '.meta/config.json')['files']
                    for example in config['example']:
                        path = folder / example
                        if task['language'] in ('Python', 'JavaScript'):
                            dest = config['solution'][0]
                        else:
                            dest = next(n for n in config['solution'] if Path(n).suffix == path.suffix)
                        drv.copy_file(helper, container, path, '/app/' + dest)
            grades[variant] = drv.grade_control(task, container, out / variant)
        finally:
            container.remove(force=True)
            client.close()
    row = {'id': task['id'], 'ready': not grades['starter']['passed'] and grades['reference']['passed'], **grades}
    save(out / 'preflight.json', row)
    print('Preflight', task['id'], row['ready'], flush=True)
    return row


def repository_grade(task, agent, patch, run_id, skip_patch=False):
    import docker
    from swebench.harness.run_evaluation import run_instance
    from swebench.harness.utils import make_test_spec
    row = next(r for r in load(PRIVATE / 'repository-dataset.json') if r['instance_id'] == task['id'])
    client = docker.from_env(timeout=1800)
    try:
        result = run_instance(make_test_spec(row), {'instance_id': task['id'], 'model_name_or_path': agent,
                              'model_patch': patch}, client, run_id, timeout=900, skip_patch=skip_patch)
        return result
    finally:
        client.close()


def preflight(available_only=False):
    import docker
    client = docker.from_env()
    network = client.networks.create(NETWORK, internal=True)
    drv = driver()
    tasks = load(PRIVATE / ('tasks.json' if (PRIVATE / 'tasks.json').exists() else 'tasks.pending.json'))
    if available_only:
        ready = []
        for task in tasks:
            if task['id'] == 'terminal-headless-terminal':
                task['image'] = 'aster/general50-headless:20261012'
            try:
                image = client.images.get(task['image'])
            except docker.errors.ImageNotFound:
                continue
            task['image_id'] = image.id
            ready.append(task)
        tasks = ready
    os.chdir(PRIVATE)

    def one(task):
        if task['kind'] != 'repository':
            return local_preflight(drv, task)
        out = PRIVATE / 'preflight' / task['id'] / 'preflight.json'
        if out.exists():
            return load(out)
        row = next(r for r in load(PRIVATE / 'repository-dataset.json') if r['instance_id'] == task['id'])
        reports = {}
        for variant in ('starter', 'reference'):
            run_id = 'general50-preflight-' + variant
            repository_grade(task, variant, row['patch'] if variant == 'reference' else '', run_id,
                             skip_patch=variant == 'starter')
            path = PRIVATE / 'logs/evaluation' / run_id / variant / task['id'] / 'report.json'
            reports[variant] = load(path)[task['id']] if path.exists() else {}
        coverage = all(set(json.loads(row[g]) if isinstance(row[g], str) else row[g]) <=
                       set(r.get('tests_status', {}).get(g, {}).get('success', []) +
                           r.get('tests_status', {}).get(g, {}).get('failure', []))
                       for r in reports.values() for g in ('FAIL_TO_PASS', 'PASS_TO_PASS'))
        result = {'id': task['id'], 'ready': coverage and bool(reports['reference'].get('resolved'))
                  and not reports['starter'].get('resolved') and not any(r.get('infra_failure') for r in reports.values()),
                  'required_tests_observed': coverage, **reports}
        save(out, result)
        print('Preflight', task['id'], result['ready'], flush=True)
        return result
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            checks = list(pool.map(one, tasks))
        result = {'ready': len(checks) == 50 and all(r['ready'] for r in checks), 'tasks': checks}
        save(HERE / 'preflight.json', result)
        print('All preflight ready:', result['ready'], flush=True)
    finally:
        network.remove()
        client.close()


def freeze():
    assert load(HERE / 'preflight.json')['ready']
    assert load(HERE / 'environment-preflight.json')['ready']
    original = load(HERE.parent / 'general-coding-20261010/manifest.json')
    assert hashes(PRIVATE / 'runtime/aster') == original['product_hashes']
    assert not any((PRIVATE / 'runs').glob('*/launch.json'))
    tasks = load(PRIVATE / 'tasks.json')
    files = [*HERE.glob('*.py'), HERE / 'PROTOCOL.md', PRIVATE / 'tasks.json', PRIVATE / 'repository-dataset.json',
             DATA / 'selection.json', DATA / 'sources.json', HERE / 'preflight.json',
             HERE / 'environment-preflight.json',
             PRIVATE / 'python-packages.txt', DATA / 'javascript-package-lock.json',
             HERE.parent / 'general-coding-20261010/evaluate.py', HERE.parent / 'general-coding-20261010/summarize.py',
             baseline.CODEX / 'bin/codex', PRIVATE / 'pi/node_modules/@earendil-works/pi-coding-agent/dist/bundle/cli.js']
    files += [baseline.ORIGINAL / (n + '.py') for n in ('compare', 'gateway', 'relay')]
    files += [p for folder in ('terminal-tasks', 'aider-tasks', 'library-tasks')
              for p in (PRIVATE / 'sources' / folder).rglob('*') if p.is_file()]
    # Store external binary hashes separately so all relative paths remain within the repository.
    external = [p for p in files if not p.is_relative_to(REPO)]
    files = [p for p in files if p.is_relative_to(REPO)]
    manifest = {'aster_commit': original['aster_commit'],
                'source_policy': 'Immutable c6b651d snapshot captured at task start; concurrent workspace edits excluded.',
                'model': 'gpt-5.6-luna', 'reasoning': 'medium', 'seconds_per_task': 900, 'concurrency': CONCURRENCY,
                'context_tokens': 64000, 'output_tokens': 8192, 'attempts_per_task': 1,
                'pi_version': '1.1.0', 'codex_version': '0.154.0', 'tasks': [t['id'] for t in tasks],
                'planned_episodes': 150, 'product_hashes': hashes(PRIVATE / 'runtime/aster'),
                'runtime_hashes': hashes(PRIVATE / 'runtime'),
                'input_hashes': {str(p.relative_to(REPO)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files},
                'external_binary_hashes': {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in external}}
    save(HERE / 'manifest.json', manifest)
    verify_frozen()
    print('Frozen 50 tasks and 150 attempts.', flush=True)


def environment_preflight(available_only=False):
    import docker
    drv = driver()
    client = docker.from_env()
    drv.NETWORK = NETWORK + '-environment'
    # start() uses the outer module's NETWORK through original_start's globals.
    network = client.networks.create(drv.NETWORK, internal=True)
    tasks = load(PRIVATE / ('tasks.json' if (PRIVATE / 'tasks.json').exists() else 'tasks.pending.json'))
    if available_only:
        ready = []
        for task in tasks:
            if task['id'] == 'terminal-headless-terminal':
                task['image'] = 'aster/general50-headless:20261012'
            try:
                image = client.images.get(task['image'])
            except docker.errors.ImageNotFound:
                continue
            task['image_id'] = image.id
            ready.append(task)
        tasks = ready

    def one(task):
        record = PRIVATE / 'environment-preflight' / (task['id'] + '.json')
        if record.exists() and load(record).get('image_id') == task['image_id']:
            return load(record)
        connection, container = drv.start_container(task, 'env-' + task['id'])
        try:
            env = {**drv.environment(task, 'environment'), 'PYTHONPATH': '/opt/agent-code',
                   'BENCH_IMPORT': IMPORTS.get(task.get('repo'), '')}
            if task['kind'] == 'library':
                env['BENCH_IMPORT'] = 'numpy, pandas, matplotlib.pyplot, scipy, sklearn'
            result = container.exec_run([PYTHON, '/opt/agent-code/environment_check.py'], environment=env)
            record.parent.mkdir(exist_ok=True)
            record.with_suffix('.log').write_bytes(result.output)
            row = {'id': task['id'], 'ready': result.exit_code == 0, 'image_id': task['image_id']}
            save(record, row)
            print('Environment', task['id'], row['ready'], flush=True)
            return row
        finally:
            container.remove(force=True)
            connection.close()
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            checks = list(pool.map(one, tasks))
        save(HERE / 'environment-preflight.json', {'ready': len(checks) == 50 and all(r['ready'] for r in checks), 'tasks': checks})
    finally:
        network.remove()
        client.close()


def gateway():
    module = baseline.module('gateway')
    module.PRIVATE = PRIVATE
    assert module.SETTINGS.model == 'gpt-5.6-luna'
    class Server(ThreadingMixIn, UnixStreamServer):
        daemon_threads = True
    with Server(str(PRIVATE / 'relay/model.sock'), module.Gateway) as server:
        os.chmod(PRIVATE / 'relay/model.sock', 0o666)
        server.serve_forever()


def run():
    import docker
    verify_frozen()
    drv = driver()
    tasks = load(PRIVATE / 'tasks.json')
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
            time.sleep(0.1)
        assert socket.exists()
        relay = client.containers.run(tasks[0]['image_id'], [PYTHON, '/relay.py'], detach=True,
            name='aster-bench-gateway', network=NETWORK,
            volumes={str(PRIVATE / 'relay'): {'bind': '/relay', 'mode': 'rw'},
                     str(baseline.ORIGINAL / 'relay.py'): {'bind': '/relay.py', 'mode': 'ro'},
                     str(Path(PYTHON).parent.parent): {'bind': str(Path(PYTHON).parent.parent), 'mode': 'ro'}})
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            smokes = list(pool.map(lambda a: drv.episode(a, tasks[0], smoke=True), AGENTS))
        assert all(r['smoke_passed'] for r in smokes), 'Integration smoke failed; formal attempts not started'
        ordered = sorted(tasks, key=lambda t: hashlib.sha256(('schedule:' + t['id']).encode()).hexdigest())
        jobs = [(AGENTS[(index + offset) % 3], task) for index, task in enumerate(ordered) for offset in range(3)]
        save(HERE / 'schedule.json', [{'agent': a, 'task': t['id']} for a, t in jobs])

        def one(agent, task):
            result = drv.episode(agent, task)
            if task['kind'] == 'repository':
                patch = (PRIVATE / 'runs' / result['job'] / 'prediction.patch').read_text()
                repository_grade(task, agent, patch, 'general50-' + agent, skip_patch=not patch)
            return result
        summary_helpers = imported('general50_usage', HERE.parent / 'general-coding-20261010/summarize.py')
        # Keep a bounded queue so an actual provider outage cannot spend all 150 attempts.
        recent_outages = []
        iterator = iter(jobs)
        with concurrent.futures.ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
            pending = {pool.submit(one, *next(iterator)) for _ in range(CONCURRENCY)}
            while pending:
                done, pending = concurrent.futures.wait(pending, return_when=concurrent.futures.FIRST_COMPLETED)
                for future in done:
                    result = future.result()
                    _, requests = summary_helpers.usage(PRIVATE / 'runs' / result['job'])
                    recent_outages.append(bool(requests) and all(
                        r.get('error') in {'ReadTimeout', 'ConnectTimeout', 'ConnectError', 'RemoteProtocolError'}
                        or r.get('http_status', 200) >= 500 or r.get('http_status') == 429
                        or bool(r.get('stream_error')) for r in requests))
                if len(recent_outages) >= 6 and all(recent_outages[-6:]):
                    save(HERE / 'provider-outage.json', {'reason': 'six consecutive attempts had only failed provider requests',
                                                        'time': time.time(), 'finished_before_pause': len(recent_outages)})
                    raise RuntimeError('Provider outage; remaining attempts were not submitted')
                for _ in done:
                    job = next(iterator, None)
                    if job is not None:
                        pending.add(pool.submit(one, *job))
        verify_frozen()
    finally:
        if relay is not None:
            relay.remove(force=True)
        if process is not None:
            process.terminate()
            process.wait(timeout=10)
        socket.unlink(missing_ok=True)
        network.remove()
        client.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('preflight', 'environment_preflight', 'freeze', 'run', 'gateway'))
    parser.add_argument('--available-only', action='store_true')
    args = parser.parse_args()
    if args.mode in ('preflight', 'environment_preflight'):
        globals()[args.mode](args.available_only)
    else:
        globals()[args.mode]()
