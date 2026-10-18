"""Lay out the fresh30b pool like general-50, pull images, pin ids. No model calls."""
import concurrent.futures
import hashlib
import json
import shutil
import sys
import tarfile
from pathlib import Path

import docker

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
PRIVATE = REPO / 'artifacts/private' / HERE.name
G50 = REPO / 'artifacts/private/general-coding-50-20261012'
G10 = REPO / 'artifacts/private/general-coding-20261010'
sys.path.insert(0, str(HERE.parent / 'final-repair-20261012'))
from grading_reset import repair_script  # noqa: E402

TERMINAL_CANDIDATES = 6  # preflight keeps the first two ready ones in rank order


def load(path):
    return json.loads(Path(path).read_text())


def save(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')


def main(start=0, stop=TERMINAL_CANDIDATES):
    """start>0 appends further ranked terminal candidates to an already provisioned pool."""
    for name in ('task-python', 'pi', 'node-v22.22.0-linux-x64', 'js'):
        link = PRIVATE / name
        if not link.exists():
            link.symlink_to((G50 / name).resolve(), target_is_directory=True)
    if not (PRIVATE / 'runtime').exists():
        shutil.copytree(G50 / 'runtime', PRIVATE / 'runtime', ignore=shutil.ignore_patterns('__pycache__'))
    extend = start > 0
    tasks = [] if extend else load(PRIVATE / 'tasks.pending.json')
    candidates = load(PRIVATE / 'terminal-candidates.json')[start:stop]
    with tarfile.open(G10 / 'sources/terminal-bench.tar.gz') as bundle:
        for row in candidates:
            prefix = None
            for member in bundle.getmembers():
                parts = member.name.split('/')
                if len(parts) > 3 and parts[1] == 'archive' and parts[2] == row['name']:
                    prefix = prefix or '/'.join(parts[:3])
                    target = PRIVATE / 'sources/terminal-tasks' / row['name'] / '/'.join(parts[3:])
                    if member.isdir():
                        target.mkdir(parents=True, exist_ok=True)
                    elif member.isfile():
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(bundle.extractfile(member).read())
            folder = PRIVATE / 'sources/terminal-tasks' / row['name']
            tasks.append({'id': 'terminal-' + row['name'], 'kind': 'terminal', 'source': 'Terminal-Bench archive',
                          'image': row['config']['environment']['docker_image'], 'workspace': '/app',
                          'difficulty': row.get('difficulty', 'unlabelled'), 'task_name': row['name'],
                          'previously_used': False, 'exposure': 'new30', 'pool': 'fresh30b', 'language': 'mixed',
                          'prompt': (folder / 'instruction.md').read_text(), 'test_python': '/opt/task-python/bin/python',
                          'terminal_candidate': True})
    client = docker.from_env(timeout=1800)
    images = sorted({t['image'] for t in tasks})

    def pull(tag):
        try:
            image = client.images.get(tag)
        except docker.errors.ImageNotFound:
            try:
                image = client.images.pull(tag)
            except docker.errors.APIError as exc:
                return tag, {'tag': tag, 'error': str(exc)[:300]}
        print('Ready', tag, flush=True)
        return tag, {'tag': tag, 'id': image.id, 'digests': image.attrs.get('RepoDigests', [])}

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        resolved = dict(pool.map(pull, images))
    ready = []
    for task in tasks:
        image = resolved[task['image']]
        if 'error' in image:
            task['unavailable'] = image['error']
            continue
        task['prompt_sha256'] = hashlib.sha256(task['prompt'].encode()).hexdigest()
        task.update(image_id=image['id'], image_digests=image['digests'])
        ready.append(task)
    if extend:
        save(PRIVATE / 'tasks.provisioned.json', load(PRIVATE / 'tasks.provisioned.json') + ready)
        save(PRIVATE / 'images.json', {**load(PRIVATE / 'images.json'), **resolved})
        print('Appended', len(ready), 'terminal candidates; no model calls.')
        return
    rows = load(PRIVATE / 'repository-original.json')
    for row in rows:
        row['image'] = resolved[row['image']]['id']
        row['eval_script'] = repair_script(row['eval_script'])
    save(PRIVATE / 'repository-dataset.json', rows)
    save(PRIVATE / 'tasks.provisioned.json', ready)
    save(PRIVATE / 'images.json', resolved)
    print('Provisioned', len(ready), 'of', len(tasks), 'tasks; no model calls.')


if __name__ == '__main__':
    main(*(int(a) for a in sys.argv[1:3]))
