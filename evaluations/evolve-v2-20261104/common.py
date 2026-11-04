"""Shared paths and run loading for the self-evolution experiment (docs/evolve-plan.md). No model calls."""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
V12 = REPO / 'evaluations/harness-v12-20261017'
PUBLIC = V12 / 'runs'
PRIVATE = REPO / 'artifacts/private/harness-v12-20261017'
WORK = REPO / 'artifacts/private/evolve-v2-20261104'
CONTROLLER = REPO / 'artifacts/private/swebench-compare-20261008/controller-env/bin/python'
DEV_POOL = 'general-coding-50-20261012'
HELDOUT_POOL = 'fresh30c-20261102'
SEED = 'evolve2-s1'
MODEL_ARGS = ['--model', 'deepseek-flash', '--protocol', 'chat',
              '--key-env', 'DEEPSEEKAPI_KEY', '--base-url-env', 'DEEPSEEKBASE_URL',
              '--acceptance', 'benchmark']


def load(path):
    return json.loads(Path(path).read_text())


def save(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')


def dev_tasks():
    tasks = [t for t in load(REPO / 'artifacts/private' / DEV_POOL / 'tasks.json')
             if t['kind'] == 'repository' and 'pool' not in t]
    assert len(tasks) == 20, 'Expected the frozen 20 old20/new30 repository tasks'
    return tasks


def weighted(row):
    cached = row['cached_tokens']
    return ((row['input_tokens'] - cached) * 1.25 + cached * 0.125 + row['output_tokens'] * 10) / 1e6


def run_rows(name):
    """Per-task rows of a finished run. Cost with missing usage is filled at the task's own
    known per-request mean (REPORT §15.2 convention), so a provider gap is not counted as free."""
    rows = {}
    for row in load(PUBLIC / name / 'results.json'):
        known = row['requests'] - row['missing_usage']
        cost = weighted(row)
        if row['missing_usage'] and known > 0:
            cost = cost * row['requests'] / known
        rows[row['task']] = {**row, 'cost': cost,
                             'unreliable': row['stop_reason'] == 'provider_error' or row['request_errors'] >= 3
                             or known <= 0}
    return rows
