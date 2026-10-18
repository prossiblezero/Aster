"""Select the fresh30b held-out set, drawn before any v14 change is evaluated on it. No model calls.

Same composition and seeded-hash ranking as fresh30, with a new seed and every previously used
task (including all of fresh30) excluded. Nothing about agent outcomes enters the selection.
Tasks carry exposure 'new30' (the general-50 engine keys its small/library setup on it) and
pool 'fresh30b' (the v12 runner's --tasks fresh30b selector).
"""
import ast
import hashlib
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
PRIVATE = REPO / 'artifacts/private' / HERE.name
G50 = REPO / 'artifacts/private/general-coding-50-20261012'
G10 = REPO / 'artifacts/private/general-coding-20261010'
DATA = REPO / 'datasets' / HERE.name
SEED = '20261018b:'
FRESH30 = REPO / 'datasets/fresh30-20261018'
SWE_REPOS = ['django/django', 'sympy/sympy', 'sphinx-doc/sphinx', 'pydata/xarray', 'pytest-dev/pytest',
             'astropy/astropy', 'scikit-learn/scikit-learn', 'matplotlib/matplotlib']


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')


def load(path):
    return json.loads(path.read_text())


def rank(name):
    return hashlib.sha256((SEED + name).encode()).hexdigest()


def main():
    assert not PRIVATE.exists(), 'Already prepared; the selection is frozen'
    used = load(G50 / 'tasks.json')
    used_ids = {t['id'] for t in used} | {t.get('source_id') for t in used if t.get('source_id')}
    previous = load(REPO / 'datasets/general-coding-20261010/selection.json')
    used_ids |= {s.removeprefix('worker-code-v1__') for s in previous['repository']['excluded_previous_ids']}
    used_slugs = {t['task_name'] for t in used if t.get('task_name')}
    first = load(FRESH30 / 'tasks.json')  # every fresh30 task, by id, source id and slug
    used_ids |= {t['id'] for t in first} | {t['source_id'] for t in first if t.get('source_id')}
    used_slugs |= {t['task_name'] for t in first if t.get('task_name')}
    used_ids |= {t['id'] for t in first if t['kind'] == 'terminal'}
    tasks, selection = [], {'seed': SEED, 'excluded_count': len(used_ids)}

    rows = load(REPO / 'artifacts/private/swebench-verified-20261008/rows.json')
    selected_rows = []
    selection['repository'] = {}
    for repo in SWE_REPOS:
        candidates = sorted((r for r in rows if r['repo'] == repo and r['instance_id'] not in used_ids
                             and r['difficulty'] != '>4 hours'), key=lambda r: rank(r['instance_id']))
        row = candidates[0]
        selection['repository'][repo] = {'ranked': [r['instance_id'] for r in candidates[:20]], 'selected': row['instance_id']}
        selected_rows.append(row)
        tasks.append({'id': row['instance_id'], 'kind': 'repository', 'source': 'SWE-bench Verified',
                      'image': row['image'], 'workspace': '/testbed', 'base_commit': row['base_commit'],
                      'repo': row['repo'], 'difficulty': row['difficulty'], 'previously_used': False,
                      'exposure': 'new30', 'pool': 'fresh30b', 'language': 'Python', 'prompt': row['problem_statement'],
                      'test_python': '/opt/miniconda3/envs/testbed/bin/python'})

    selection['aider'] = {}
    for language, count in (('python', 2), ('javascript', 4), ('cpp', 4)):
        catalog = G50 / 'sources/aider-catalog' / language / 'exercises/practice'
        candidates = sorted((p for p in catalog.iterdir() if language + '-' + p.name not in used_slugs),
                            key=lambda p: rank(language + '/' + p.name))
        selection['aider'][language] = {'ranked': [p.name for p in candidates], 'selected': [p.name for p in candidates[:count]]}
        for folder in candidates[:count]:
            name = language + '-' + folder.name
            shutil.copytree(folder, PRIVATE / 'sources/aider-tasks' / name)
            prompt = (folder / '.docs/instructions.md').read_text()
            if (folder / '.docs/instructions.append.md').exists():
                prompt += '\n\n' + (folder / '.docs/instructions.append.md').read_text()
            tasks.append({'id': 'aider-' + name, 'kind': 'small', 'source': 'Aider Polyglot',
                          'image': ('aster/general50-tools-slim:20261012' if language == 'cpp'
                                    else 'alexgshaw/cancel-async-tasks:20251031'), 'workspace': '/app',
                          'task_name': name, 'difficulty': 'unlabelled', 'previously_used': False,
                          'exposure': 'new30', 'pool': 'fresh30b', 'language': {'python': 'Python', 'javascript': 'JavaScript', 'cpp': 'C++'}[language],
                          'prompt': prompt, 'test_python': '/opt/task-python/bin/python',
                          'files': load(folder / '.meta/config.json')['files']})

    bigrows = load(G50 / 'sources/bigcodebench.json')
    allowed = set(sys.stdlib_module_names) | {'numpy', 'pandas', 'scipy', 'sklearn', 'matplotlib', 'seaborn',
                                             'PIL', 'bs4', 'lxml', 'yaml', 'faker'}
    blocked = {'requests', 'urllib', 'http', 'ftplib', 'smtplib', 'socket', 'subprocess', 'multiprocessing',
               'tkinter', 'turtle', 'unittest', 'pytest'}
    candidates = sorted((r for r in bigrows if r['task_id'] not in used_ids
                         and set(ast.literal_eval(r['libs'])) <= allowed
                         and not set(ast.literal_eval(r['libs'])) & blocked), key=lambda r: rank(r['task_id']))
    selection['bigcodebench'] = {'ranked': [r['task_id'] for r in candidates[:40]], 'selected': [r['task_id'] for r in candidates[:10]]}
    for row in candidates[:10]:
        name = 'bigcodebench-' + row['task_id'].split('/')[1]
        folder = PRIVATE / 'sources/library-tasks' / name
        folder.mkdir(parents=True)
        save(folder / 'record.json', row)
        (folder / 'solution.py').write_text(row['code_prompt'] + '    pass\n')
        (folder / 'reference.py').write_text(row['complete_prompt'] + row['canonical_solution'])
        (folder / 'test_solution.py').write_text(row['test'])
        tasks.append({'id': name, 'kind': 'library', 'source': 'BigCodeBench v0.1.4', 'source_id': row['task_id'],
                      'image': 'alexgshaw/cancel-async-tasks:20251031', 'workspace': '/app', 'task_name': name,
                      'difficulty': 'unlabelled', 'previously_used': False, 'exposure': 'new30', 'pool': 'fresh30b', 'language': 'Python',
                      'prompt': row['instruct_prompt'] + '\nImplement task_func in solution.py.\n' + row['complete_prompt'],
                      'test_python': '/opt/task-python/bin/python', 'libraries': ast.literal_eval(row['libs'])})

    # Terminal-Bench: rank all unused tasks; preflight later keeps the first two that are ready.
    catalog = load(G10 / 'sources/terminal-catalog.json')
    # Same pool as the earlier sets: archived tasks with a prebuilt image and a 900 s agent budget
    # (the catalog also holds QA fixtures and newer tasks that need hour-long builds).
    candidates = sorted((r for r in catalog if r['path'].startswith('archive/')
                         and r['config'].get('environment', {}).get('docker_image')
                         and r['config'].get('agent', {}).get('timeout_sec', 10**9) <= 900
                         and 'terminal-' + r['name'] not in used_ids and r['name'] not in used_slugs),
                        key=lambda r: rank('terminal/' + r['name']))
    selection['terminal'] = {'ranked': [r['name'] for r in candidates[:12]],
                             'rule': 'first two in rank order whose image pulls and whose starter fails and reference passes'}
    save(PRIVATE / 'terminal-candidates.json', candidates[:12])

    for task in tasks:
        task['prompt_sha256'] = hashlib.sha256(task['prompt'].encode()).hexdigest()
    save(PRIVATE / 'tasks.pending.json', tasks)
    save(PRIVATE / 'repository-original.json', selected_rows)
    save(DATA / 'selection.json', selection)
    print('Selected', len(tasks), 'non-terminal tasks; terminal candidates ranked; no model calls.')
    for task in tasks:
        print(' ', task['id'], task['language'], task.get('difficulty'))


if __name__ == '__main__':
    main()
