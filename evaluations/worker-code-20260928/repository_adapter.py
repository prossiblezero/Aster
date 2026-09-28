"""Frozen SWE task replay using original tests in independent restricted copies.

The controller's private rows come from the pinned dataset parquet. Python 3.9
is an aster-derived Conda environment; this is a native equivalent replay, not
an official Docker score. No gold or hidden tests are copied into model trees.
"""
from __future__ import annotations

import ast
import hashlib
import importlib.machinery
import importlib.util
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PRIVATE = REPO / 'artifacts/private/worker-code-20260928'
DEPENDENCY_ROOT = Path('/tmp/aster-worker-repository-py39-20260928')
TEST_PYTHON = DEPENDENCY_ROOT / 'bin/python'
OFFICIAL_PARSER_SHA256 = 'cd56156414f8327221e525665ace9b184f7d73e83b272d9eb3f545fb17c2d9bc'


def _objects(cache: Path) -> Path:
    return cache / 'objects' if (cache / 'objects').is_dir() else cache


def _asset(cache: Path, digest: str) -> Path:
    path = _objects(cache) / digest
    if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        raise ValueError(f'cache checksum mismatch: {digest}')
    return path


def _row(task: dict, cache: Path | None = None) -> dict:
    if task['source'] == 'swebench-pro':
        if cache is None:
            cache = REPO / 'artifacts/private/worker-code-v2/objects'
        assets = {x['path']: _asset(cache, x['asset']) for x in task['materials']['grader']}
        config = json.loads(assets['tests/config.json'].read_text())
        return {'instance_id': task['upstream_id'], 'repo': task['origin']['repo'],
                'patch': assets['solution/gold_patch.diff'].read_text(), 'test_patch': assets['tests/test_patch.patch'].read_text(),
                'FAIL_TO_PASS': ast.literal_eval(config['fail_to_pass']), 'PASS_TO_PASS': ast.literal_eval(config['pass_to_pass']),
                'selected_tests': ast.literal_eval(config['selected_test_files_to_run']),
                'run_script': assets['tests/run_script.sh'].read_text(), 'pro_parser': str(assets['tests/parser.py'])}
    row = json.loads((PRIVATE / 'repository_rows.json').read_text())[task['upstream_id']]
    for field, key in [('patch', 'reference_sha256'), ('test_patch', 'test_patch_sha256'), ('eval_script', 'eval_script_sha256')]:
        if hashlib.sha256(row[field].encode()).hexdigest() != task['grading'][key]:
            raise ValueError(f'row checksum mismatch: {task["id"]} {field}')
    return row


def _base(task: dict, cache: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    archive = next(x for x in task['materials']['setup'] if x['path'] == 'base.tar.gz')
    with tarfile.open(_asset(cache, archive['asset'])) as source:
        for member in source.getmembers():
            parts = Path(member.name).parts
            if len(parts) > 1:
                member.name = str(Path(*parts[1:]))
                source.extract(member, target, filter='data')


def _runtime(task: dict) -> Path:
    if task['origin']['repo'] == 'internetarchive/openlibrary':
        return Path('/tmp/aster-openlibrary-py311-20260928')
    if task['origin']['repo'] == 'flipt-io/flipt':
        return Path('/tmp/aster-flipt-go-20260928')
    return DEPENDENCY_ROOT


def _public_dependencies(task: dict, workspace: Path) -> None:
    if task['origin']['repo'] == 'internetarchive/openlibrary':
        metadata = json.loads((PRIVATE / 'openlibrary-infogami-source.json').read_text())
        archive = PRIVATE / 'openlibrary-infogami.tar.gz'
        if hashlib.sha256(archive.read_bytes()).hexdigest() != metadata['sha256']:
            raise ValueError('infogami submodule checksum mismatch')
        destination = workspace / 'vendor/infogami'
        with tarfile.open(archive) as source:
            for member in source.getmembers():
                parts = Path(member.name).parts
                if len(parts) > 1:
                    member.name = str(Path(*parts[1:]))
                    source.extract(member, destination, filter='data')
        # Workspace tools omit symlinks; materialize this same public subtree.
        alias = workspace / 'infogami'
        if alias.is_symlink():
            alias.unlink()
            shutil.copytree(destination / 'infogami', alias)


def prepare(task: dict, cache: Path, workspace: Path) -> dict:
    runtime = _runtime(task)
    if not (runtime / 'bin/python').exists():
        return {'status': 'environment_unavailable', 'ready': False, 'reason': f'Missing prepared interpreter {runtime / "bin/python"}', 'task_id': task['id']}
    _row(task, cache)
    _base(task, cache, workspace)
    _public_dependencies(task, workspace)
    public_commands = {
        'django/django': 'PYTHONPATH=. python tests/runtests.py --settings=test_sqlite --parallel 1 <test_label>; choose labels from the public tests/ tree.',
        'sphinx-doc/sphinx': 'PYTHONPATH=. python -m pytest <public_test_path>; choose tests from the public tests/ tree.',
        'internetarchive/openlibrary': 'PYTHONPATH=. python -m pytest <public_test_path>; choose tests from the public openlibrary/ tree.',
        'flipt-io/flipt': 'go test ./<public_package>; choose packages from the public source tree. Go modules are pre-cached; keep GOPROXY=off.',
        'sympy/sympy': 'PYTHONPATH=. python bin/test -C --verbose <public_test_path>; choose tests from the public sympy/ tree.',
    }
    metadata = {'status': 'ready', 'ready': True, 'task_id': task['id'], 'test_python': str(runtime / 'bin/python'), 'dependency_root': str(runtime),
            'prompt_suffix': '\nThe repository is checked out at the requested base revision. Dependencies are preinstalled; execution is offline. Use the preconfigured python and dependency paths. Public test guidance: ' + public_commands[task['origin']['repo']],
            'environment_kind': 'native_equivalent_original_tests', 'requires_reference_gate': True}
    if task['origin']['repo'] == 'flipt-io/flipt':
        metadata['execution_env'] = {'GOROOT': str(runtime / 'go'), 'GOPATH': str(runtime / 'gopath'), 'GOMODCACHE': str(runtime / 'gopath/pkg/mod'), 'GOPROXY': 'off', 'GOSUMDB': 'off', 'GOCACHE': '{workspace}/.tmp/go-build'}
        metadata['extra_path'] = [str(runtime / 'go/bin')]
        metadata['sandbox_file_limit_bytes'] = 512 * 1024 * 1024
        metadata['sandbox_cpu_seconds'] = 300
    lock = PRIVATE / ('openlibrary-py311-pip-freeze.txt' if task['origin']['repo'] == 'internetarchive/openlibrary' else 'flipt-pip-freeze.txt' if task['origin']['repo'] == 'flipt-io/flipt' else 'repository-py39-pip-freeze.txt')
    if lock.exists():
        metadata['dependency_lock_sha256'] = hashlib.sha256(lock.read_bytes()).hexdigest()
    return metadata


def _patch(workspace: Path, patch: str) -> None:
    subprocess.run(['git', 'init', '-q', str(workspace)], check=True)
    result = subprocess.run(['git', 'apply', '--whitespace=nowarn', '-'], cwd=workspace, input=patch, text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError('patch application failed: ' + result.stderr[-3000:])


def _test_paths(patch: str) -> list[str]:
    return [line[6:] for line in patch.splitlines() if line.startswith('+++ b/')]


def _command(row: dict) -> str:
    script = row['eval_script']
    command = script.split(": '>>>>> Start Test Output'\n", 1)[1].split(": '>>>>> End Test Output'", 1)[0].strip()
    command = command.replace('./tests/runtests.py ', 'python tests/runtests.py ')
    command = command.replace("bin/test -C", "python bin/test -C")
    if command.startswith('tox --current-env -epy39 -v -- '):
        command = 'python -X dev -m pytest -rA -v ' + command.split(' -- ', 1)[1]
    return command


def _parse(log: str, row: dict) -> dict[str, str]:
    if row.get('pro_parser'):
        spec = importlib.util.spec_from_file_location('aster_pro_parser', row['pro_parser'], loader=importlib.machinery.SourceFileLoader('aster_pro_parser', row['pro_parser']))
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        statuses = {r.name: r.status.name for r in module.parse_test_output(log, '')}
        if row['repo'] == 'flipt-io/flipt':
            # Upstream emits only passes; retain failed-test observations too.
            for name in re.findall(r'^--- FAIL: (\S+) \(', log, re.MULTILINE):
                statuses[name] = 'FAILED'
        return statuses
    # The exact official parser functions are saved from the pinned harness.
    artifact = PRIVATE / 'official_log_parsers.py'
    source = artifact.read_text()
    if hashlib.sha256(source.encode()).hexdigest() != OFFICIAL_PARSER_SHA256:
        raise ValueError('official parser checksum mismatch')
    tree = ast.parse(source)
    wanted = {'_is_skip_summary', 'parse_log_django', 'parse_log_sympy', 'parse_log_pytest', 'parse_log_pytest_v2'}
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in wanted]
    from enum import Enum
    class TestStatus(Enum):
        PASSED = 'PASSED'
        FAILED = 'FAILED'
        SKIPPED = 'SKIPPED'
        ERROR = 'ERROR'
    namespace = {'re': re, 'TestStatus': TestStatus, 'TestSpec': dict, '_SKIP_SUMMARY_COUNT': re.compile(r'^\[\d+\]$')}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(artifact), 'exec'), namespace)
    if row['repo'] == 'django/django':
        # Python 3.9 unittest prints docstrings on a second line. Preserve the
        # original test ID when feeding the unchanged upstream log parser.
        log += '\n' + re.sub(r'(?m)^(test\S+ \([^\n]+\))\n[^\n]*? \.\.\. (ok|OK|FAIL|ERROR|skipped[^\n]*)$', r'\1 ... \2', log)
    parser = row['log_parser']
    if parser == 'parse_log_sphinx':
        parser = 'parse_log_pytest_v2'
    return namespace[parser](log, row)



def _restore_tests(target: Path, pristine: Path, row: dict) -> list[str]:
    """Restore public test infrastructure as well as the hidden patch surface."""
    code_dirs = {'tests', 'test', '__tests__', 'spec', 'specs'}
    data_dirs = {'testdata', 'test_data', 'fixtures', 'fixture', 'mocks', 'mock', '__snapshots__'}
    configs = {'conftest.py', 'pytest.ini', 'tox.ini', 'setup.cfg', 'sitecustomize.py', 'usercustomize.py'}

    def protected(path: Path) -> bool:
        parts, name = path.parts, str(path)
        if row.get('pro_parser'):
            parents = set(parts[:-1])
            if parents & data_dirs:
                return name != 'openlibrary/mocks/mock_infobase.py'
            return bool(parents & code_dirs or path.name == 'conftest.py'
                        or re.search(r'_test\.(go|py|js|jsx|ts|tsx)$|^test_.*\.py$|\.(test|spec)\.(js|jsx|ts|tsx)$', path.name))
        if path.name in configs:
            return True
        if row['repo'] == 'django/django':
            return parts[0] == 'tests'
        if row['repo'] == 'sphinx-doc/sphinx':
            return parts[0] == 'tests' or parts[:2] == ('sphinx', 'testing')
        return 'tests' in parts[:-1] or name in {
            'bin/test', 'bin/get_sympy.py', 'sympy/utilities/runtests.py',
            'sympy/utilities/pytest.py', 'sympy/conftest.py',
        }

    paths = {p.relative_to(root) for root in (target, pristine) for p in root.rglob('*') if p.is_file() and '.git' not in p.parts}
    discarded = []
    for relative in sorted(paths):
        if not protected(relative):
            continue
        source, destination = pristine / relative, target / relative
        if source.is_file():
            if not destination.is_file() or destination.read_bytes() != source.read_bytes():
                discarded.append(str(relative))
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        elif destination.exists():
            discarded.append(str(relative))
            destination.unlink()
    return discarded


def grade(task: dict, cache: Path, workspace: Path, output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    row = _row(task, cache)
    runtime = _runtime(task)
    test_python = runtime / 'bin/python'
    with tempfile.TemporaryDirectory(prefix='aster-repository-grade-') as temporary:
        root = Path(temporary)
        target = root / 'workspace'
        # Do not follow model-created symlinks into control-plane material.
        shutil.copytree(workspace, target, symlinks=True, ignore=shutil.ignore_patterns('.git', '.aster', '__pycache__', '.pytest_cache', '.tmp'))
        for path in target.rglob('*'):
            if path.is_symlink():
                if not path.resolve().is_relative_to(target):
                    raise ValueError('candidate contains escaping symlink')
        pristine = root / 'pristine'
        _base(task, cache, pristine)
        if task['source'] == 'swebench-pro':
            _public_dependencies(task, pristine)
        discarded_tests = _restore_tests(target, pristine, row)
        for relative in _test_paths(row['test_patch']):
            destination = target / relative
            original = pristine / relative
            if destination.is_dir():
                shutil.rmtree(destination)
            elif destination.exists() or destination.is_symlink():
                destination.unlink()
            if original.exists():
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(original, destination)
        shutil.rmtree(pristine)
        _patch(target, row['test_patch'])
        (target / '.tmp').mkdir(exist_ok=True)
        env = {'PATH': f'{runtime}/bin:{runtime}/go/bin:/usr/bin:/bin', 'HOME': str(target), 'TMPDIR': str(target / '.tmp'), 'XDG_CACHE_HOME': str(target / '.tmp'),
               'PYTHONPATH': str(target), 'PYTHONNOUSERSITE': '1', 'PYTHONDONTWRITEBYTECODE': '1', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8'}
        if row.get('pro_parser'):
            verifier = target / '.verifier'
            verifier.mkdir()
            script = row['run_script'].replace('export PYTHONPATH=/app', 'export PYTHONPATH=' + shlex.quote(str(target)))
            (verifier / 'run_script.sh').write_text(script)
            command = 'bash .verifier/run_script.sh ' + shlex.quote(','.join(row['selected_tests']))
            if row['repo'] == 'flipt-io/flipt':
                env.update(GOPATH=str(runtime / 'gopath'), GOCACHE=str(target / '.tmp/go-build'), GOPROXY='off', GOSUMDB='off')
        else:
            command = _command(row)
        launcher = REPO / 'src/aster/safety/linux.py'
        if row['repo'] == 'flipt-io/flipt':
            source = launcher.read_text().replace('(16 * 1024 * 1024, 16 * 1024 * 1024)', '(512 * 1024 * 1024, 512 * 1024 * 1024)').replace('(120, 120)', '(300, 300)')
            launcher = root / 'linux.py'
            launcher.write_text(source)
        # Only the mail verifier needs local SMTP servers. Agent execution stays offline.
        network = 'on' if row['repo'] == 'django/django' and row['instance_id'].endswith('-11532') else 'off'
        argv = [str(test_python), '-I', str(launcher), str(target), network, command]
        log_path = output / 'tests.log'
        timed_out = False
        with log_path.open('w') as log:
            process = subprocess.Popen(argv, cwd=target, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            try:
                code = process.wait(timeout=600)
            except subprocess.TimeoutExpired:
                timed_out = True
                os.killpg(process.pid, signal.SIGKILL)
                code = process.wait()
        text = log_path.read_text(errors='replace')
        statuses = _parse(text, row)
        f2p, p2p = row['FAIL_TO_PASS'], row['PASS_TO_PASS']
        required = set(f2p) | set(p2p)
        missing = sorted(required - statuses.keys())
        passed = not timed_out and code != 125 and not missing and all(statuses[k] == 'PASSED' for k in required)
        result = {'status': 'passed' if passed else ('environment_error' if code == 125 or missing else 'failed'), 'passed': passed, 'exit_code': code, 'timed_out': timed_out,
                  'fail_to_pass': {k: statuses.get(k, 'MISSING') for k in f2p}, 'pass_to_pass': {k: statuses.get(k, 'MISSING') for k in p2p},
                  'required_count': len(required), 'observed_count': len(statuses), 'missing_tests': missing, 'test_statuses': statuses, 'non_scoring_failures': {k: v for k, v in statuses.items() if k not in required and v in {'FAILED', 'ERROR'}},
                  'discarded_test_changes': discarded_tests, 'test_integrity_policy': 'restore_public_tests_and_runners_v2', 'test_command': command, 'python': str(test_python), 'network': network, 'network_note': 'Grader-only SMTP socket access; sandbox does not restrict destinations.' if network == 'on' else 'Network blocked', 'sandbox': 'Landlock+seccomp', 'environment_kind': 'native_equivalent_original_tests', 'mode': 'original_f2p_p2p_native_equivalent'}
        (output / 'grade.json').write_text(json.dumps(result, indent=2) + '\n')
        return result


def preflight(task: dict, cache: Path, output: Path) -> dict:
    """Reuse the completed starter/reference gate, or perform it once."""
    output.mkdir(parents=True, exist_ok=True)
    prior = PRIVATE / 'repository-preflight' / (task['id'] + '.json')
    adapter_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    cached = json.loads(prior.read_text()) if prior.exists() else {}
    if cached.get('adapter_sha256') == adapter_hash:
        result = cached
        result.update(ready=result.get('status') == 'ready', mode='original_f2p_p2p_native_equivalent')
    else:
        with tempfile.TemporaryDirectory(prefix='aster-repository-preflight-') as directory:
            root = Path(directory)
            workspace = root / 'starter'
            metadata = prepare(task, cache, workspace)
            result = {'id': task['id'], 'prepare': metadata, 'ready': False, 'mode': 'original_f2p_p2p_native_equivalent'}
            if metadata['status'] == 'ready':
                result['starter'] = grade(task, cache, workspace, output / 'starter')
                _patch(workspace, _row(task, cache)['patch'])
                result['reference'] = grade(task, cache, workspace, output / 'reference')
                result['ready'] = bool(not result['starter']['passed'] and not result['starter']['missing_tests'] and result['reference']['passed'])
            result['status'] = 'ready' if result['ready'] else 'environment_unavailable'
    result['adapter_sha256'] = adapter_hash
    result['start'] = result.get('starter')
    result['ref'] = result.get('reference')
    (output / 'preflight.json').write_text(json.dumps(result, indent=2) + '\n')
    return result
