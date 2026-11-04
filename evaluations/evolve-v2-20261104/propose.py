"""One proposer step: Aster, running the base candidate's own code, edits that candidate's profile.

  propose.py NAME --base TAG --runs RUN... [--sibling PROPOSAL_MD]

Builds an isolated workspace (profile, scores, rendered dev trajectories, history), runs the
Aster CLI one-shot inside it, then applies the guards from docs/evolve-plan.md §4:
  * only profile.json and proposal.md may change in the workspace;
  * the profile must pass aster.context.profile.validate and grow by at most 15% in characters;
  * added text may not name any development task, repository or exercise.
A passing proposal becomes commit + tag evolve-NAME on top of the base, via a temporary worktree
(the branch is not moved). The proposer sees no held-out task, grader output, docs or git history:
Landlock limits its reads to the workspace copy and system directories.
"""
import argparse
import hashlib
import io
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import tarfile
import time
from pathlib import Path

from common import HERE, REPO, SEED, WORK, dev_tasks, load, run_rows, save
import traces

PYTHON = '/home/dzh/.conda/envs/aster/bin/python'
GROWTH = 1.15
PROMPT = """You are improving the harness profile of a coding agent. The agent is Aster, the same
harness you are running in now. profile.json holds the model-visible wording Aster uses: `policy`
and `main_policy` (system prompt text placed after a fixed safety preamble) and `tools` (the
descriptions of the nine default tools), plus `control` (six completion/workflow feedback strings).
The control strings explain unresolved failures and required verification. Test names, commands,
counts and conditional suffixes are still assembled by code. Parameter schemas, permissions, the
sandbox and completion checks are fixed code; only this wording can change.

Evidence in this workspace:
- scores.md: per-task outcome of the current profile on development tasks over several runs
  (passed, model requests, weighted cost, stop reason).
- traces/<run>/<task>.txt: the agent's trajectories with the current profile.
- history.md: earlier candidate profiles, what they changed and whether evaluation accepted them.
- profile.json: the current profile. Edit this file.
- seed_profile.json: the starting profile, for reference only.

Goal: lower the agent's number of model requests on unseen coding tasks without lowering the
pass rate or normal delivery rate. Both you and the task solver use deepseek-flash.

Method:
1. Read scores.md. Select the five distinct tasks with the highest mean request counts, and read
   each corresponding trajectory before editing. Then inspect some cheap tasks for contrast.
2. Read their traces. Find recurring behavior that wastes turns or tokens and that wording could
   plausibly change, for example redundant reads, serial calls that could be batched, repeated
   test runs, broad exploration, chasing unrelated failures, or misunderstanding a tool. Check
   failed tasks for mistakes the wording may have caused.
3. Make one to three small, targeted edits to profile.json that address patterns you saw in at
   least three different tasks per edit. Prefer rewording or removing text that misleads or is ignored over
   adding rules. The profile may grow by at most 15% in characters.
4. Write proposal.md: for each edit give the pattern, at least three distinct task ids with
   evidence, the exact edit, and the risk. Also list the five high-request tasks you inspected.

Rules:
- Edits must be task-agnostic. Never mention task names, repositories, file names, function names
  or test names from the traces; the agent will be judged on different tasks.
- Do not weaken safety or verification: keep the requirements to verify changes and to report
  actual results honestly.
- Keep profile.json valid JSON with the same keys; change only string values.
- Do not create or modify any file other than profile.json and proposal.md.
"""
SIBLING = """
A sibling proposal in this generation already made the edits below. Investigate a different
pattern and make different edits.

{text}
"""


def git(*args, **kwargs):
    return subprocess.run(['git', *args], cwd=kwargs.pop('cwd', REPO), check=True, text=True,
                          capture_output=True, **kwargs).stdout


def profile_at(ref):
    return json.loads(git('show', f'{ref}:src/aster/context/profile.json'))


def characters(profile):
    return (len(profile['policy']) + len(profile['main_policy'])
            + sum(map(len, profile['tools'].values())) + sum(map(len, profile['control'].values())))


def manifest(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob('*')) if p.is_file() and '.aster' not in p.relative_to(root).parts}


def identifiers():
    names = set()
    for task in dev_tasks():
        names.add(task['id'])
        for key in ('task_name', 'source_id', 'repo'):
            if task.get(key):
                names.add(task[key])
                names.update(task[key].split('/'))
        if task['id'].startswith('aider-'):
            names.add(task['id'].split('-', 2)[2])  # exercise slug, e.g. pig-latin
        if task['id'].startswith('terminal-'):
            names.add(task['id'].removeprefix('terminal-'))
    return {name.lower() for name in names if len(name) >= 4}


def leaks(base, new):
    before = json.dumps(base).lower()
    after = json.dumps(new).lower()
    return sorted(name for name in identifiers()
                  if re.search(r'(?<![a-z0-9])' + re.escape(name) + r'(?![a-z0-9])', after) and name not in before)


def scores(runs):
    allowed = {task['id'] for task in dev_tasks()}
    tables = {run: {task: row for task, row in run_rows(run).items() if task in allowed} for run in runs}
    tasks = sorted(set.union(*(set(t) for t in tables.values())))
    lines = ['# Development scores with the current profile', '',
             'cost is weighted USD-equivalent: (uncached input x 1.25 + cached input x 0.125 + output x 10) / 1e6.',
             'Tasks marked provider_error ended on provider failures, not on the agent.', '',
             '| task | ' + ' | '.join(f'{r}: passed / requests / cost / stop' for r in runs) + ' | mean requests |',
             '| --- |' + ' --- |' * (len(runs) + 1)]
    rows = []
    for task in tasks:
        cells, requests = [], []
        for run in runs:
            row = tables[run].get(task)
            if row is None:
                cells.append('missing')
                continue
            requests.append(row['requests'])
            cells.append(f"{'yes' if row['passed'] else 'NO'} / {row['requests']} / {row['cost']:.3f} / {row['stop_reason']}")
        rows.append((statistics.mean(requests), f"| {task} | " + ' | '.join(cells) + f" | {statistics.mean(requests):.3f} |"))
    lines += [line for _, line in sorted(rows, reverse=True)]
    for run in runs:
        table = tables[run].values()
        lines.append(f"\n{run}: passed {sum(r['passed'] for r in table)}/{len(table)}, "
                     f"requests {sum(r['requests'] for r in table)}, cost {sum(r['cost'] for r in table):.3f}")
    return '\n'.join(lines) + '\n'


def history():
    log = WORK / 'log.json'
    if not log.exists():
        return '# History\n\nNo earlier candidates.\n'
    lines = ['# History', '']
    for entry in load(log):
        lines += [f"## {entry['name']} (base {entry['base']}): {entry['decision']}", entry.get('summary', ''), '']
    return '\n'.join(lines)


def export(ref, target):
    archive = subprocess.check_output(['git', 'archive', ref, 'src/aster'], cwd=REPO)
    with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
        for item in bundle:
            if item.isfile():
                path = target / 'aster' / Path(item.name).relative_to('src/aster')
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(bundle.extractfile(item).read())


def commit_candidate(name, base, profile):
    tree = WORK / 'worktree'
    assert not tree.exists(), 'Inspect the unfinished candidate worktree before continuing'
    git('worktree', 'add', '--detach', str(tree), base)
    try:
        path = tree / 'src/aster/context/profile.json'
        path.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + '\n')
        test = subprocess.run([PYTHON, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', 'tests/test_profile.py',
                               'tests/test_prompt_cache.py'],
                              cwd=tree, capture_output=True, text=True, env={**os.environ, 'PYTHONPATH': str(tree / 'src')})
        if test.returncode:
            return None, test.stdout[-2000:]
        git('add', 'src/aster/context/profile.json', cwd=tree)
        staged = git('diff', '--cached', '--name-only', cwd=tree).splitlines()
        assert staged == ['src/aster/context/profile.json']
        git('commit', '-q', '-m', f'evolve2: DeepSeek candidate {name}', cwd=tree)
        commit = git('rev-parse', 'HEAD', cwd=tree).strip()
        git('tag', 'evolve2-' + name, commit)
        return commit, test.stdout[-300:]
    finally:
        git('worktree', 'remove', '--force', str(tree))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('name')
    parser.add_argument('--base', required=True)
    parser.add_argument('--runs', nargs='+', required=True)
    parser.add_argument('--sibling', type=Path)
    parser.add_argument('--max-turns', type=int, default=60)
    args = parser.parse_args()
    root = WORK / args.name
    assert not root.exists(), 'Proposal directory exists; choose a new name'
    space = root / 'workspace'
    space.mkdir(parents=True)
    base = profile_at(args.base)
    (space / 'profile.json').write_text(json.dumps(base, ensure_ascii=False, indent=2) + '\n')
    (space / 'seed_profile.json').write_text(json.dumps(profile_at(SEED), ensure_ascii=False, indent=2) + '\n')
    (space / 'scores.md').write_text(scores(args.runs))
    (space / 'history.md').write_text(history())
    for run in args.runs:
        traces.main(run, space / 'traces' / run)
    before = manifest(space)
    export(args.base, root / 'harness')
    prompt = PROMPT + (SIBLING.format(text=args.sibling.read_text()) if args.sibling else '')
    (root / 'prompt.txt').write_text(prompt)
    started = time.time()
    with (root / 'proposer.log').open('w') as log:
        code = subprocess.run([PYTHON, '-c', 'import sys; from aster.cli import main; sys.exit(main())', prompt, '--cwd', str(space), '--env-file', str(REPO / '.env'),
                               '--config', str(WORK / 'deepseek.toml'), '--profile', 'deepseek',
                               '--sandbox', 'required', '--approval', 'auto', '--max-turns', str(args.max_turns),
                               '--max-steps', str(args.max_turns * 3)],
                              cwd=root, stdout=log, stderr=subprocess.STDOUT,
                              env={**os.environ, 'PYTHONPATH': str(root / 'harness')}).returncode
    after = manifest(space)
    usage, runtime = {}, {}
    for path in (space / '.aster/runs').glob('*/report.json'):
        runtime = load(path)
        usage = runtime.get('usage', {})
    cached = usage.get('cache_read_input_tokens', 0)
    usage_cost = ((usage.get('input_tokens', 0) - cached) * 1.25 + cached * 0.125
                  + usage.get('output_tokens', 0) * 10) / 1e6
    changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
    report = {'name': args.name, 'base': args.base, 'runs': args.runs, 'exit_code': code,
              'seconds': round(time.time() - started, 1), 'changed': changed, 'problems': [],
              'proposer_requests': usage.get('usage_requests'), 'proposer_cost': round(usage_cost, 6),
              'usage': usage, 'stop_reason': runtime.get('stop_reason'),
              'model': 'deepseek-flash', 'protocol': 'chat',
              'started': started, 'ended': time.time()}
    if set(changed) - {'profile.json', 'proposal.md'}:
        report['problems'].append('changed files outside profile.json/proposal.md')
    if 'proposal.md' not in after:
        report['problems'].append('no proposal.md')
    try:
        sys.path.insert(0, str(root / 'harness'))
        from aster.context.profile import validate
        new = validate(json.loads((space / 'profile.json').read_text()))
    except (ValueError, json.JSONDecodeError) as exc:
        new = None
        report['problems'].append(f'invalid profile: {exc}')
    if new is not None:
        report['characters'] = {'base': characters(base), 'new': characters(new)}
        if new == base:
            report['problems'].append('profile unchanged')
        if characters(new) > GROWTH * characters(base):
            report['problems'].append('profile grew more than 15%')
        report['leaks'] = leaks(base, new)
        if report['leaks']:
            report['problems'].append('names development tasks: ' + ', '.join(report['leaks']))
    if not report['problems']:
        commit, tests = commit_candidate(args.name, args.base, new)
        report.update(commit=commit, tests=tests)
        if commit is None:
            report['problems'].append('candidate tests failed')
    report['accepted_for_evaluation'] = not report['problems']
    save(root / 'report.json', report)
    save(HERE / 'decisions' / (args.name + '-proposal.json'),
         {**report, 'proposal': (space / 'proposal.md').read_text() if (space / 'proposal.md').exists() else ''})
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
