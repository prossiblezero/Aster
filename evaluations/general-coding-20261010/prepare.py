"""Materialize the selected source records and freeze public metadata; no model calls."""
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
PRIVATE = REPO / 'artifacts/private' / HERE.name
DATA = REPO / 'datasets' / HERE.name


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def main():
    assert subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=REPO).decode().startswith('c6b651d')
    rows = json.loads((PRIVATE / 'repository-dataset.json').read_text())
    tasks = []
    for row in rows:
        tasks.append({'id': row['instance_id'], 'kind': 'repository', 'source': 'SWE-bench Verified',
                      'image': row['image'], 'workspace': '/testbed', 'base_commit': row['base_commit'],
                      'repo': row['repo'], 'difficulty': row['difficulty'], 'previously_used': False,
                      'prompt': row['problem_statement'],
                      'test_python': '/opt/miniconda3/envs/testbed/bin/python'})
    for row in json.loads((PRIVATE / 'sources/terminal-selected.json').read_text()):
        name = row['name']
        tasks.append({'id': 'terminal-' + name, 'kind': 'terminal', 'source': 'Terminal-Bench archive',
                      'image': row['config']['environment']['docker_image'], 'workspace': '/app',
                      'difficulty': row['difficulty'], 'task_name': name,
                      'previously_used': name in {'cancel-async-tasks', 'cobol-modernization', 'kv-store-grpc'},
                      'prompt': (PRIVATE / 'sources/terminal-tasks' / name / 'instruction.md').read_text(),
                      'test_python': '/opt/task-python/bin/python'})
    for name in json.loads((PRIVATE / 'sources/aider-selection.json').read_text())['selected']:
        folder = PRIVATE / 'sources/aider-tasks' / name
        prompt = (folder / '.docs/instructions.md').read_text()
        if (folder / '.docs/instructions.append.md').exists():
            prompt += '\n\n' + (folder / '.docs/instructions.append.md').read_text()
        tasks.append({'id': 'aider-' + name, 'kind': 'small', 'source': 'Aider Polyglot Python',
                      'image': 'alexgshaw/cancel-async-tasks:20251031', 'workspace': '/app',
                      'task_name': name, 'difficulty': 'unlabelled', 'previously_used': False,
                      'prompt': prompt, 'test_python': '/opt/task-python/bin/python'})
    assert len(tasks) == len({t['id'] for t in tasks}) == 20
    for task in tasks:
        name = task.get('task_name', task['id']) if task['kind'] != 'small' else 'cancel-async-tasks'
        pull = PRIVATE / 'image-pulls' / (name + '.json')
        image = json.loads(pull.read_text())
        assert image['ready'], image
        task['image_id'] = image['image_id']
        task['image_digests'] = image['digests']
        task['prompt_sha256'] = hashlib.sha256(task['prompt'].encode()).hexdigest()
    for name in ('aster_episode.py',):
        shutil.copy2(HERE / name, PRIVATE / 'runtime' / name)
    public = [{k: v for k, v in task.items() if k != 'prompt'} for task in tasks]
    save(PRIVATE / 'tasks.json', tasks)
    save(DATA / 'tasks.json', public)
    save(DATA / 'selection.json', {
        'repository': json.loads((PRIVATE / 'repository-selection.json').read_text()),
        'aider': json.loads((PRIVATE / 'sources/aider-selection.json').read_text()),
        'terminal': {'selection': 'Curated coverage of six workspace-code task types, no collaboration criterion.',
                     'names': [t['task_name'] for t in tasks if t['kind'] == 'terminal'],
                     'exclusions': {'build-cython-ext': 'global package installation and dependency migration',
                                    'pypi-server': 'service deployment and global package installation',
                                    'fix-git': 'Git history recovery without code implementation',
                                    'write-compressor': 'requires data.comp artifact, not code delivery',
                                    'sqlite-db-truncate': 'database artifact manipulation, not code delivery'}}})
    sources = {name: json.loads((PRIVATE / 'sources' / (name + '.json')).read_text())
               for name in ('terminal-bench', 'aider-polyglot')}
    sources['swebench'] = {'rows_sha256': digest(REPO / 'artifacts/private/swebench-verified-20261008/rows.json'),
                          'provenance': 'evaluations/swebench-verified-20261008 / cached 500 official records',
                          'harness_commit': '02e7a74ffd0b707aab73d203fe87bdc7c76afc8e'}
    save(DATA / 'sources.json', sources)
    print('Prepared 20 tasks: 12 repository, 6 terminal, 2 small; 3 historical and 17 new to Aster.')


if __name__ == '__main__':
    main()
