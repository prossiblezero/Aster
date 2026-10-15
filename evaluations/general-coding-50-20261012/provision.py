"""Resolve images and provision offline test dependencies, without model calls."""
import concurrent.futures
import hashlib
import io
import json

import docker
from prepare import DATA, PRIVATE, load, save


def main():
    tasks = load(PRIVATE / 'tasks.pending.json')
    client = docker.from_env(timeout=1800)
    images = sorted({t['image'] for t in tasks if t['image'] != 'aster/general50-tools-slim:20261012'})

    def pull(tag):
        try:
            image = client.images.get(tag)
        except docker.errors.ImageNotFound:
            print('Pulling', tag, flush=True)
            image = client.images.pull(tag)
        row = {'tag': tag, 'id': image.id, 'digests': image.attrs.get('RepoDigests', [])}
        save(PRIVATE / 'image-pulls' / (hashlib.sha256(tag.encode()).hexdigest() + '.json'), row)
        print('Ready', tag, flush=True)
        return tag, row

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        resolved = dict(pool.map(pull, images))
    for tag, base, packages in [
        ('aster/general50-tools-slim:20261012', 'alexgshaw/cancel-async-tasks:20251031', 'g++ cmake libboost-date-time-dev'),
        ('aster/general50-headless:20261012', 'alexgshaw/headless-terminal:20251031', 'vim'),
    ]:
        dockerfile = ('FROM ' + resolved[base]['id'] + '\nRUN apt-get update && apt-get install -y --no-install-recommends '
                      + packages + ' && rm -rf /var/lib/apt/lists/*\n')
        (PRIVATE / (tag.split('/')[1].replace(':', '-') + '.Dockerfile')).write_text(dockerfile)
        try:
            image = client.images.get(tag)
        except docker.errors.ImageNotFound:
            with (PRIVATE / (tag.split('/')[1].replace(':', '-') + '.build.jsonl')).open('w') as log:
                for item in client.api.build(fileobj=io.BytesIO(dockerfile.encode()), tag=tag, rm=True, decode=True):
                    log.write(json.dumps(item) + '\n')
                    log.flush()
                    if 'error' in item:
                        raise RuntimeError(item['error']) from None
            image = client.images.get(tag)
        resolved[tag] = {'tag': tag, 'id': image.id, 'digests': image.attrs.get('RepoDigests', []),
                         'dockerfile_sha256': hashlib.sha256(dockerfile.encode()).hexdigest()}
        print('Built', tag, flush=True)
    for task in tasks:
        if task['kind'] == 'library':
            task['test_python'] = '/opt/task-python/bin/python'
        if task['id'] == 'terminal-headless-terminal':
            task['upstream_image'] = task['image']
            task['image'] = 'aster/general50-headless:20261012'
        image = resolved[task['image']]
        task.update(image_id=image['id'], image_digests=image['digests'])
    save(PRIVATE / 'tasks.json', tasks)
    save(DATA / 'tasks.json', [{k: v for k, v in t.items() if k != 'prompt'} for t in tasks])
    save(DATA / 'images.json', resolved)
    client.close()
    print('Provisioned 50 task images.', flush=True)


if __name__ == '__main__':
    main()
