"""Select the 50 tasks before model calls; reuse pinned upstream records."""
import ast
import hashlib
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
PRIVATE = REPO / 'artifacts/private' / HERE.name
BASE = REPO / 'artifacts/private/general-coding-20261010'
DATA = REPO / 'datasets' / HERE.name
SEED = '20261012:'


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')


def load(path):
    return json.loads(path.read_text())


def rank(name):
    return hashlib.sha256((SEED + name).encode()).hexdigest()


def main():
    assert not any((PRIVATE / 'runs').glob('*/launch.json'))
    tasks = load(BASE / 'tasks.json')
    for task in tasks:
        task.update(previously_used=True, exposure='old20', language='Python')
        if task['kind'] == 'small':
            task['source'] = 'Aider Polyglot'
    rows = load(REPO / 'artifacts/private/swebench-verified-20261008/rows.json')
    selected_rows = load(BASE / 'repository-dataset.json')
    previous = load(REPO / 'datasets/general-coding-20261010/selection.json')
    excluded = {s.removeprefix('worker-code-v1__') for s in previous['repository']['excluded_previous_ids']}
    excluded.update(t['id'] for t in tasks)
    selection = {'seed': SEED, 'old20': [t['id'] for t in tasks], 'repository': {}, 'aider': {}, 'bigcodebench': {}}
    for repo in ('astropy/astropy', 'scikit-learn/scikit-learn', 'matplotlib/matplotlib', 'pylint-dev/pylint'):
        candidates = sorted((r for r in rows if r['repo'] == repo and r['instance_id'] not in excluded),
                            key=lambda r: rank(r['instance_id']))
        short = [r for r in candidates if r['difficulty'] in ('<15 min fix', '15 min - 1 hour')]
        long = [r for r in candidates if r['difficulty'] in ('1-4 hours', '>4 hours')]
        selected = [short[0], (long or short[1:])[0]]
        selection['repository'][repo] = {'ranked': [r['instance_id'] for r in candidates],
                                        'selected': [r['instance_id'] for r in selected]}
        for row in selected:
            selected_rows.append(row)
            tasks.append({'id': row['instance_id'], 'kind': 'repository', 'source': 'SWE-bench Verified',
                          'image': row['image'], 'workspace': '/testbed', 'base_commit': row['base_commit'],
                          'repo': row['repo'], 'difficulty': row['difficulty'], 'previously_used': False,
                          'exposure': 'new30', 'language': 'Python', 'prompt': row['problem_statement'],
                          'test_python': '/opt/miniconda3/envs/testbed/bin/python'})
    for name in ('terminal-tasks', 'aider-tasks'):
        shutil.copytree(BASE / 'sources' / name, PRIVATE / 'sources' / name, dirs_exist_ok=True)
    catalog = load(BASE / 'sources/terminal-catalog.json')
    for name in ('headless-terminal', 'regex-chess'):
        row = next(r for r in catalog if r['name'] == name)
        folder = PRIVATE / 'sources/terminal-tasks' / name
        tasks.append({'id': 'terminal-' + name, 'kind': 'terminal', 'source': 'Terminal-Bench archive',
                      'image': row['config']['environment']['docker_image'], 'workspace': '/app',
                      'difficulty': row['difficulty'], 'task_name': name, 'previously_used': False,
                      'exposure': 'new30', 'language': 'Python', 'prompt': (folder / 'instruction.md').read_text(),
                      'test_python': '/opt/task-python/bin/python'})
    selection['terminal'] = {'selected': ['headless-terminal', 'regex-chess'],
                             'method': 'Coverage of interactive PTY programming and constrained regex compilation; no agent outcome or decomposition criterion.'}
    excluded_slugs = {s.split('/')[-1] for s in excluded if '/' in s}
    excluded_slugs.update(t['task_name'] for t in tasks if t['kind'] == 'small')
    for language, count in (('python', 2), ('javascript', 4), ('cpp', 4)):
        catalog_path = PRIVATE / 'sources/aider-catalog' / language / 'exercises/practice'
        candidates = sorted((p for p in catalog_path.iterdir() if p.name not in excluded_slugs),
                            key=lambda p: rank(language + '/' + p.name))
        selected = candidates[:count]
        selection['aider'][language] = {'ranked': [p.name for p in candidates], 'selected': [p.name for p in selected]}
        for folder in selected:
            name = language + '-' + folder.name
            target = PRIVATE / 'sources/aider-tasks' / name
            shutil.copytree(folder, target, dirs_exist_ok=True)
            prompt = (folder / '.docs/instructions.md').read_text()
            if (folder / '.docs/instructions.append.md').exists():
                prompt += '\n\n' + (folder / '.docs/instructions.append.md').read_text()
            config = load(folder / '.meta/config.json')['files']
            tasks.append({'id': 'aider-' + name, 'kind': 'small', 'source': 'Aider Polyglot',
                          'image': ('aster/general50-tools-slim:20261012' if language == 'cpp'
                                    else 'alexgshaw/cancel-async-tasks:20251031'), 'workspace': '/app', 'task_name': name,
                          'difficulty': 'unlabelled', 'previously_used': False, 'exposure': 'new30',
                          'language': {'python': 'Python', 'javascript': 'JavaScript', 'cpp': 'C++'}[language],
                          'prompt': prompt, 'test_python': '/opt/task-python/bin/python', 'files': config})
    bigrows = load(PRIVATE / 'sources/bigcodebench.json')
    allowed = set(sys.stdlib_module_names) | {'numpy', 'pandas', 'scipy', 'sklearn', 'matplotlib', 'seaborn',
                                             'PIL', 'bs4', 'lxml', 'yaml', 'faker'}
    blocked = {'requests', 'urllib', 'http', 'ftplib', 'smtplib', 'socket', 'subprocess', 'multiprocessing',
               'tkinter', 'turtle', 'unittest', 'pytest'}
    candidates = sorted((r for r in bigrows if r['task_id'] not in excluded
                         and set(ast.literal_eval(r['libs'])) <= allowed
                         and not set(ast.literal_eval(r['libs'])) & blocked), key=lambda r: rank(r['task_id']))
    selected = candidates[:10]
    selection['bigcodebench'] = {'allowed_libraries': sorted(allowed), 'blocked_libraries': sorted(blocked),
                                'ranked': [r['task_id'] for r in candidates], 'selected': [r['task_id'] for r in selected],
                                'eligibility': 'Offline CPU tasks using stdlib or the dependency allowlist; install libraries needed by selected tasks; exclude previous task IDs.'}
    for row in selected:
        name = 'bigcodebench-' + row['task_id'].split('/')[1]
        folder = PRIVATE / 'sources/library-tasks' / name
        folder.mkdir(parents=True, exist_ok=True)
        save(folder / 'record.json', row)
        (folder / 'solution.py').write_text(row['code_prompt'] + '    pass\n')
        (folder / 'reference.py').write_text(row['complete_prompt'] + row['canonical_solution'])
        (folder / 'test_solution.py').write_text(row['test'])
        tasks.append({'id': name, 'kind': 'library', 'source': 'BigCodeBench v0.1.4', 'source_id': row['task_id'],
                      'image': 'alexgshaw/cancel-async-tasks:20251031', 'workspace': '/app', 'task_name': name,
                      'difficulty': 'unlabelled', 'previously_used': False, 'exposure': 'new30',
                      'language': 'Python', 'prompt': row['instruct_prompt'] + '\nImplement task_func in solution.py.\n'
                          + row['complete_prompt'], 'test_python': '/opt/task-python/bin/python',
                      'libraries': ast.literal_eval(row['libs'])})
    assert len(tasks) == len({t['id'] for t in tasks}) == 50
    for task in tasks:
        task['prompt_sha256'] = hashlib.sha256(task['prompt'].encode()).hexdigest()
        if task['id'] == 'terminal-polyglot-c-py':
            task['language'] = 'C/Python'
        elif task['id'] == 'terminal-custom-memory-heap-crash':
            task['language'] = 'C++'
        elif task['id'] == 'terminal-schemelike-metacircular-eval':
            task['language'] = 'Scheme'
        elif task['id'] == 'terminal-cobol-modernization':
            task['language'] = 'COBOL/Python'
    save(PRIVATE / 'tasks.pending.json', tasks)
    save(PRIVATE / 'repository-original.json', selected_rows)
    save(DATA / 'selection.json', selection)
    print('Selected', len(tasks), 'tasks; no model calls.')
    for task in tasks[20:]:
        print(task['id'], task['language'], task.get('libraries', ''))


if __name__ == '__main__':
    main()
