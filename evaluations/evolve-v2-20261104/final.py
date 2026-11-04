"""One final analysis of the four pre-registered heldout runs; no model calls."""
import argparse
import re
import subprocess

import analyze
from common import HELDOUT_POOL, HERE, PUBLIC, REPO, SEED, load, save


def leakage(ref):
    original = subprocess.check_output(
        ['git', 'show', f'{SEED}:src/aster/context/profile.json'], cwd=REPO, text=True,
    ).lower()
    diff = subprocess.check_output(
        ['git', 'diff', '--unified=0', SEED, ref, '--', 'src/aster/context/profile.json'],
        cwd=REPO, text=True,
    )
    added = '\n'.join(line[1:] for line in diff.splitlines()
                      if line.startswith('+') and not line.startswith('+++')).lower()
    names = set()
    for task in load(REPO / 'artifacts/private' / HELDOUT_POOL / 'tasks.json'):
        names.add(task['id'])
        for key in ('task_name', 'source_id', 'repo'):
            if task.get(key):
                names.add(task[key])
                names.update(task[key].split('/'))
        if task['id'].startswith('aider-'):
            names.add(task['id'].split('-', 2)[2])
        if task['id'].startswith('terminal-'):
            names.add(task['id'].removeprefix('terminal-'))
    patterns = {n: r'(?<![a-z0-9])' + re.escape(n.lower()) + r'(?![a-z0-9])'
                for n in names if len(n) >= 4}
    hits = sorted(n for n, pattern in patterns.items()
                  if re.search(pattern, added) and not re.search(pattern, original))
    return {'base': SEED, 'final': ref, 'checked': 'identifiers in added profile lines absent from seed',
            'identifier_count': len(names), 'matches': hits, 'passed': not hits}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ref', required=True)
    args = parser.parse_args()
    candidates = [f'evo2-ho-final-r{r}' for r in (1, 2)]
    seeds = [f'evo2-ho-seed-r{r}' for r in (1, 2)]
    for run, ref in [(n, args.ref) for n in candidates] + [(n, SEED) for n in seeds]:
        m = load(PUBLIC / run / 'manifest.json')
        assert m['ref'] == ref and m['pool'] == HELDOUT_POOL and len(m['tasks']) == 30
        assert m['concurrency'] == 3 and m['model'] == 'deepseek-flash' and m['protocol'] == 'chat'
        summary = load(PUBLIC / run / 'summary.json')
        assert summary['finished'] == summary['graded'] == 30
    result = analyze.compare('heldout-final', candidates, seeds, heldout=True)
    result['leakage'] = leakage(args.ref)
    result['resume_eligible'] = (
        not result['stop'] and result['leakage']['passed']
        and result['requests']['ci90'][1] < 1 and result['passed']['ci90'][1] >= 0
    )
    save(HERE / 'decisions/heldout-final.json', result)
    import json
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
