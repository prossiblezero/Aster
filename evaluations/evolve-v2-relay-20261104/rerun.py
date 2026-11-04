"""User-authorized relay replication; reuse frozen execution and analysis helpers."""
import hashlib
import os
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE.parent / 'evolve-v2-20261104'))
import analyze
import generation
from common import HELDOUT_POOL, PRIVATE, PUBLIC, SEED, load, save
from final import leakage

WORK = REPO / 'artifacts/private' / HERE.name
MODEL = 'deepseek-v4-1-flash'
FINAL = 'evolve2-g1a'
TAG = 'evolve2-relay-manifests'
PAIRS = [{f'evo2-relay-seed-r{r}': SEED, f'evo2-relay-final-r{r}': FINAL} for r in (1, 2)]


def validate(runs, *, concurrency, pool, tasks):
    assert concurrency == 3 and pool == HELDOUT_POOL and len(runs) == 2
    assert len(tasks) == len(set(tasks)) == 30
    for name, ref in runs.items():
        path = PUBLIC / name / 'manifest.json'
        m = load(path)
        assert (m['ref'], m['model'], m['protocol'], m['concurrency'], m['pool']) == (
            ref, MODEL, 'chat', 3, HELDOUT_POOL)
        assert set(m['tasks']) == set(tasks) and m['acceptance'] == 'benchmark'
        assert m['key_env'] == 'DEEPSEEKAPI_KEY' and m['base_url_env'] == 'DEEPSEEKBASE_URL'
        assert m['seconds_per_task'] == 900 and m['context_tokens'] == 64000 and m['output_tokens'] == 8192
        assert m['aster_commit'] == subprocess.check_output(['git', 'rev-parse', ref], cwd=REPO, text=True).strip()
        assert path.read_bytes() == subprocess.check_output(['git', 'show', f'{TAG}:{path.relative_to(REPO)}'], cwd=REPO)


def main():
    assert sys.argv[1:] in (['run'], ['analyze'])
    registration = load(HERE / 'registration.json')
    for name, digest in registration['sha256'].items():
        assert hashlib.sha256((REPO / name).read_bytes()).hexdigest() == digest, name
    tasks = load(PUBLIC / next(iter(PAIRS[0])) / 'manifest.json')['tasks']
    for pair in PAIRS:
        validate(pair, concurrency=3, pool=HELDOUT_POOL, tasks=tasks)
    if sys.argv[1] == 'run':
        sys.path.insert(0, str(REPO / 'src'))
        from aster.config import read_env
        env = read_env(REPO / '.env')
        assert hashlib.sha256(env['DEEPSEEKBASE_URL'].encode()).hexdigest() == registration['endpoint_sha256']
        # Freeze the chosen endpoint/key in the parent for both pairs; never print them.
        for key in ('DEEPSEEKBASE_URL', 'DEEPSEEKAPI_KEY'):
            os.environ[key] = env[key]
        generation.HERE, generation.WORK, generation.prepare = HERE, WORK, validate
        for number, pair in enumerate(PAIRS, 1):
            print(f'Starting relay pair {number}', flush=True)
            generation.evaluate(pair, concurrency=3, pool=HELDOUT_POOL, tasks=tasks)
            print(f'Relay pair {number} complete', flush=True)
    else:
        for pair in PAIRS:
            for name in pair:
                summary = load(PUBLIC / name / 'summary.json')
                assert summary['finished'] == summary['graded'] == 30
        output = HERE / 'decisions/heldout-final.json'
        assert not output.exists(), 'Final analysis already saved; do not overwrite it'
        analyze.HERE = HERE
        result = analyze.compare('heldout-final', [f'evo2-relay-final-r{r}' for r in (1, 2)],
                                 [f'evo2-relay-seed-r{r}' for r in (1, 2)], heldout=True)
        result.update(model=MODEL, final_ref=FINAL, leakage=leakage(FINAL),
                      experiment='user-authorized relay replication; original results preserved')
        result['resume_eligible'] = (not result['stop'] and result['leakage']['passed']
                                     and result['requests']['ci90'][1] < 1 and result['passed']['ci90'][1] >= 0)
        save(output, result)
        print(output.relative_to(REPO))


if __name__ == '__main__':
    main()
