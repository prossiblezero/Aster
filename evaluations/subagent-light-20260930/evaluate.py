"""Explicit read-only subagent scenario experiment; no provider calls in preflight."""
# ruff: noqa: E402
from __future__ import annotations

import argparse
import ast
import asyncio
import difflib
import hashlib
import importlib.metadata
import importlib.util
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import time
from collections import Counter
from contextlib import contextmanager
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
DATA = REPO / 'datasets/subagent-light-20260930'
CACHE = REPO / 'artifacts/private/worker-code-v2/objects'
OUT = REPO / 'artifacts/private/subagent-light-20260930'
sys.path.insert(0, str(REPO / 'src'))
sys.path.insert(0, str(HERE.parent / 'worker-memory-20260922'))
spec = importlib.util.spec_from_file_location('light_ledger', HERE.parent / 'worker-memory-20260922/run.py')
ledger_driver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ledger_driver)
import aster
from aster.core.hooks import Hooks
from aster.core.runtime import Runtime
from aster.providers.client import ModelClient
from aster.schema import TaskSpec

ARMS = ('solo', 'serial', 'parallel')
LIMITS = dict(model_calls=36, admitted_tools=60, reported_tokens=300000, seconds=600)
save = ledger_driver.save


def sha(data):
    return hashlib.sha256(data).hexdigest()


def bank():
    manifest = json.loads((DATA / 'manifest.json').read_text())
    for name, digest in manifest['file_sha256'].items():
        if sha((DATA / name).read_bytes()) != digest:
            raise ValueError('dataset_changed: ' + name)
    tasks = json.loads((DATA / 'instances.json').read_text())
    if len(tasks) != 6 or len({t['id'] for t in tasks}) != 6:
        raise ValueError('six_unique_tasks_required')
    return tasks


def prepare(task, root):
    root.mkdir(parents=True, exist_ok=False)
    for item in task['files']:
        digest = item.get('archive', item.get('asset'))
        content = (CACHE / digest).read_bytes()
        if sha(content) != digest:
            raise ValueError('cached_asset_changed')
        if 'archive' in item:
            with tarfile.open(CACHE / digest) as archive:
                content = archive.extractfile(item['member']).read()
        if sha(content) != item['sha256']:
            raise ValueError('upstream_file_changed')
        path = root / item['path']
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    if task['kind'] == 'review':
        patches = []
        originals = {x['path']: (root / x['path']).read_text() for x in task['changes']}
        for change in task['changes']:
            path = root / change['path']
            text = path.read_text()
            if text.count(change['old']) != 1:
                raise ValueError('patch_context_not_unique')
            path.write_text(text.replace(change['old'], change['new']))
        for name, old in originals.items():
            baseline = root / 'baseline' / name
            baseline.parent.mkdir(parents=True, exist_ok=True)
            baseline.write_text(old)
            patches.extend(difflib.unified_diff(old.splitlines(True), (root / name).read_text().splitlines(True),
                                              fromfile='baseline/' + name, tofile=name))
        (root / 'candidate.diff').write_text(''.join(patches))
    (root / 'REQUEST.md').write_text(prompt(task))


def prompt(task):
    contract = ('Investigate the checked-out source without editing files. Each Python snippet is a behavior '
                'question, not a ready-made test with expected output. Reason through it using source evidence. '
                'Aliases map to complete modules loaded by file path. Every question starts with fresh module '
                'state. The answer is the value assigned to result, normalized through JSON (tuples become arrays). '
                'If evaluation raises, answer {"exception":"ExceptionClassName"}; exception text is not required. '
                'Return only JSON: {"answers":[{"id":"question id","observed":<JSON value>, '
                '"evidence":{"path":"relative candidate path","symbol":"Class.method or function",'
                '"line":<line inside that symbol>},"reason":"brief explanation"')
    if task['kind'] == 'review':
        contract += ',"regression":true_or_false'
    contract += '}]}. Include every question exactly once; no additional findings outside these probes. '
    if task['kind'] == 'review':
        contract += ('Review candidate.diff, whose author claims behavior is unchanged. baseline/ contains the '
                     'original changed files; all other files are unchanged. observed is candidate behavior, '
                     'and regression says whether the snippet differs from original behavior. Do not fix the patch. ')
    else:
        contract += 'Report actual current behavior even when it appears defective. '
    contract += ('The two work packages below are independently assigned; both must be covered. '
                 'Do not start additional subagents: the experiment controls assignment externally.\n\n')
    return contract + json.dumps({'aliases': task['aliases'], 'work_packages': task['groups']}, ensure_ascii=False, indent=2)


PROBE = '''import json,runpy,sys,types
from pathlib import Path
root=Path(sys.argv[1]); sys.path.insert(0,str(root)); data=json.load(sys.stdin)
modules={name:types.SimpleNamespace(**runpy.run_path(str(root/path))) for name,path in data['aliases'].items()}
try:
 exec(data['code'],modules)
 value=modules['result']
except Exception as exc:
 value={'exception':type(exc).__name__}
print(json.dumps(value,ensure_ascii=False))
'''


def probe(root, aliases, code):
    run = subprocess.run([sys.executable, '-B', '-c', PROBE, str(root)],
                         input=json.dumps(dict(aliases=aliases, code=code)), text=True,
                         capture_output=True, timeout=20, check=True)
    return json.loads(run.stdout)


def oracle(task, root):
    specs = json.loads((DATA / 'oracle.json').read_text())[task['id']]
    rows = []
    for question in specs:
        row = dict(question)
        row['observed'] = probe(root, task['aliases'], question['code'])
        source = (root / row['evidence_path']).read_text()
        parts = row['evidence_symbol'].split('.')
        nodes = ast.parse(source).body
        for part in parts:
            node = next(n for n in nodes if getattr(n, 'name', None) == part)
            nodes = getattr(node, 'body', [])
        row['lines'] = [node.lineno, node.end_lineno]
        row['evidence_ranges'] = {}
        def visit(nodes, prefix='', row=row):
            for entry in nodes:
                if isinstance(entry, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    name = prefix + entry.name
                    row['evidence_ranges'][name] = [entry.lineno, entry.end_lineno]
                    if isinstance(entry, ast.ClassDef):
                        visit(entry.body, name + '.')
        visit(ast.parse(source).body)
        if task['kind'] == 'review':
            with tempfile.TemporaryDirectory(prefix='aster-light-reference-') as folder:
                base = Path(folder) / 'workspace'
                original = {**task, 'kind': 'investigate', 'changes': []}
                prepare(original, base)
                row['baseline'] = probe(base, task['aliases'], question['code'])
            row['regression'] = row['observed'] != row['baseline']
        rows.append(row)
    return rows


def grade(answer, gold, review):
    try:
        text = answer.strip()
        if text.startswith('```') and text.endswith('```'):
            text = text.split('\n', 1)[1].rsplit('```', 1)[0]
        values = json.loads(text)['answers']
        if not isinstance(values, list) or any(not isinstance(x, dict) for x in values):
            raise ValueError('answers_must_be_objects')
        by_id = {x['id']: x for x in values}
        unique = len(by_id) == len(values)
    except (ValueError, KeyError, TypeError):
        return dict(passed=False, correct=0, total=len(gold), parse_error=True,
                    regressions_found=0, regressions=sum(x.get('regression', False) for x in gold),
                    false_positives=0, controls=sum(not x.get('regression', False) for x in gold) if review else 0)
    checks = []
    for expected in gold:
        found = by_id.get(expected['id'], {})
        ev = found.get('evidence', {})
        span = expected['evidence_ranges'].get(ev.get('symbol', '')) if isinstance(ev, dict) else None
        evidence = (isinstance(ev, dict) and ev.get('path') == expected['evidence_path']
                    and span and type(ev.get('line')) is int and span[0] <= ev['line'] <= span[1])
        observed = json.dumps(found.get('observed'), sort_keys=True) == json.dumps(expected['observed'], sort_keys=True)
        regression = not review or type(found.get('regression')) is bool and found['regression'] == expected['regression']
        checks.append(dict(id=expected['id'], correct=bool(evidence and observed and regression),
                           evidence=bool(evidence), observed=observed, regression=regression))
    correct = sum(x['correct'] for x in checks)
    return dict(passed=unique and set(by_id) == {x['id'] for x in gold} and correct == len(gold),
                correct=correct, total=len(gold), parse_error=False, checks=checks,
                regressions_found=sum(c['correct'] and g.get('regression', False) for c, g in zip(checks, gold, strict=True)),
                regressions=sum(x.get('regression', False) for x in gold),
                false_positives=sum(by_id.get(x['id'], {}).get('regression') is True for x in gold if review and not x['regression']),
                controls=sum(not x['regression'] for x in gold) if review else 0)


def preflight():
    records = []
    for task in bank():
        with tempfile.TemporaryDirectory(prefix='aster-light-preflight-') as folder:
            root = Path(folder) / 'workspace'
            prepare(task, root)
            gold = oracle(task, root)
            answers = [dict(id=x['id'], observed=x['observed'],
                evidence=dict(path=x['evidence_path'], symbol=x['evidence_symbol'], line=x['lines'][0]),
                **({'regression': x['regression']} if task['kind'] == 'review' else {})) for x in gold]
            positive = grade(json.dumps(dict(answers=answers)), gold, task['kind'] == 'review')
            wrong = json.loads(json.dumps(answers))
            wrong[0]['observed'] = '__wrong__'
            evidence = json.loads(json.dumps(answers))
            evidence[0]['evidence']['line'] = 0
            controls = [grade(json.dumps(dict(answers=value)), gold, task['kind'] == 'review')['passed']
                        for value in (wrong, evidence, answers[:-1], answers + [answers[0]])]
            if not positive['passed'] or any(controls):
                raise ValueError('scorer_controls_failed')
            if task['kind'] == 'review' and sum(x['regression'] for x in gold) != 4:
                raise ValueError('require_four_regressions_four_controls')
            records.append(dict(task=task['id'], ready=True, positive=positive,
                                negative_controls_passed=not any(controls), gold=gold,
                                public_hashes=ledger_driver.product_hashes(root)))
    save(OUT / 'preflight.json', records)
    save(HERE / 'PREFLIGHT.json', [{k: v for k, v in x.items() if k != 'gold'} for x in records])
    print('preflight: 6 tasks / 48 executable facts / 24 negative scorer controls passed')


def settings(arm):
    return ledger_driver.config(max_workers=0, read_only=True,
                                memory_enabled=False, memory_evolution=False, auto_dream=False)


def identity(commit):
    if 'aster' not in sys.prefix or sys.version_info[:2] != (3, 12):
        raise ValueError('use_conda_aster_or_derived_python312')
    if Path(aster.__file__).resolve() != REPO / 'src/aster/__init__.py':
        raise ValueError('wrong_import')
    if subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=REPO, text=True).strip() != commit:
        raise ValueError('wrong_source_commit')
    if subprocess.check_output(['git', 'diff', commit, '--', 'src/aster'], cwd=REPO):
        raise ValueError('source_changed')
    if subprocess.check_output(['git', 'ls-files', '--others', '--exclude-standard', 'src/aster'], cwd=REPO):
        raise ValueError('untracked_product_source')
    paths = [HERE / 'evaluate.py', HERE / 'summarize.py', HERE / 'PROTOCOL.md',
             DATA / 'manifest.json', DATA / 'instances.json', DATA / 'oracle.json',
             Path(ledger_driver.__file__), OUT / 'preflight.json']
    configs = {arm: settings(arm) for arm in ARMS}
    return dict(commit=commit, source=ledger_driver.hashes(REPO / 'src/aster','**/*.py'),
                files={str(p.relative_to(REPO)): sha(p.read_bytes()) for p in paths},
                limits=LIMITS, settings={k:v.identity() for k,v in configs.items()},
                endpoint_sha256={k:sha(v.base_url.encode()) for k,v in configs.items()},
                python=sys.executable, python_version=sys.version,
                python_sha256=sha(Path(sys.executable).read_bytes()),
                packages_sha256=sha(json.dumps(sorted((d.metadata['Name'], d.version)
                    for d in importlib.metadata.distributions())).encode()), import_path=aster.__file__)


@contextmanager
def observe(ledger, runtime):
    with ledger.observe():
        original = ModelClient.complete
        async def complete(client, messages, tools, *, on_event=None):
            index = len(ledger.requests)
            role = 'parent' if client is runtime.model else 'child'
            try:
                return await original(client, messages, tools, on_event=on_event)
            finally:
                if len(ledger.requests) > index:
                    ledger.requests[index].update(role=role, message_characters=len(json.dumps(messages,default=str)))
                    save(ledger.output / 'request-ledger.json', ledger.requests)
        ModelClient.complete = complete
        try:
            yield
        finally:
            ModelClient.complete = original


async def episode(task, arm, repeat, commit):
    frozen = json.loads((OUT / 'freeze.json').read_text())
    if identity(commit) != frozen:
        raise ValueError('frozen_inputs_changed')
    row = next(x for x in json.loads((OUT / 'preflight.json').read_text()) if x['task'] == task['id'])
    name = f"{task['id']}--{arm}--{repeat}"
    output = OUT / 'runs' / name
    output.mkdir(parents=True, exist_ok=False)
    root = OUT / 'workspaces' / name
    prepare(task, root)
    before = ledger_driver.product_hashes(root)
    if before != row['public_hashes']:
        raise ValueError('public_workspace_changed')
    ledger_driver.LIMITS = LIMITS
    ledger = ledger_driver.Ledger(name, output)
    runtime = Runtime(root, settings(arm), hooks=Hooks([ledger]))
    runtime.start(TaskSpec(prompt=prompt(task), max_steps=LIMITS['admitted_tools'],
                          max_turns=LIMITS['model_calls'], max_tokens=LIMITS['reported_tokens'],
                          require_observation=True, require_edit=False, require_verification=False))
    save(output / 'launch.json', dict(task=task['id'], arm=arm, repeat=repeat, source_commit=commit,
         settings=runtime.settings.identity(), limits=LIMITS, prompt=prompt(task), workspace=str(root)))
    assignments = []
    # Freeze identical per-child allocations to isolate scheduling; team admission remains native.
    local_allocation = runtime.workers.limits()
    runtime.workers.limits = lambda: local_allocation
    async def execute():
        if arm != 'solo':
            for group in task['groups']:
                assignment = ('Complete only work package ' + group['name'] + '. Questions: '
                              + ', '.join(q['id'] for q in group['questions'])
                              + '. Return all assigned answer rows with source evidence using the original JSON contract. '
                              'Do not answer the other package.')
                started = await runtime.workers.start(assignment, mode='explore', explicit=True,
                                    parent_work='Integrate both independent evidence reports into the final JSON response.')
                assignments.append(started)
                if started.get('worker_id'):
                    child_spec = runtime.workers.workers[started['worker_id']].runtime.state.task
                    started['local_allocation'] = dict(max_steps=child_spec.max_steps, max_turns=child_spec.max_turns)
                if started.get('status') != 'running':
                    raise RuntimeError('explicit_worker_start_failed:' + str(started))
                if arm == 'serial':
                    worker = runtime.workers.workers[started['worker_id']]
                    await asyncio.shield(worker.future)
            if arm == 'parallel':
                await asyncio.gather(*(asyncio.shield(runtime.workers.workers[x['worker_id']].future) for x in assignments))
            await runtime.workers.status([x['worker_id'] for x in assignments])
        await runtime.run()
    started = time.monotonic()
    error = None
    try:
        with observe(ledger, runtime):
            await asyncio.wait_for(execute(), LIMITS['seconds'])
    except (Exception, asyncio.CancelledError) as exc:
        error = type(exc).__name__ + ': ' + str(exc)[:300]
    finally:
        await runtime.workers.close()
        runtime.sandbox.close()
    seconds = time.monotonic() - started
    runs, tools, _, usage = ledger_driver.collect(runtime, set())
    known = Counter()
    for request in ledger.requests:
        known.update(request.get('usage', {}))
    attempts = [a for r in ledger.requests for a in r['attempts']]
    child = [x for x in runs if x.get('parent_run_id')]
    parent_requests = [x for x in ledger.requests if x.get('role') == 'parent']
    parent_inputs = [x.get('usage',{}).get('input_tokens',x.get('usage',{}).get('prompt_tokens',0)) for x in parent_requests]
    result_grade = grade(runtime.state.final, row['gold'], task['kind'] == 'review')
    parent_paths = [x['arguments'].get('path') for x in tools if x['name'] == 'read_file' and not x['child']]
    child_paths = {x['arguments'].get('path') for x in tools if x['name'] == 'read_file' and x['child']}
    intact = before == ledger_driver.product_hashes(root)
    result = dict(task=task['id'], kind=task['kind'], arm=arm, repeat=repeat, seconds=seconds,
        passed=result_grade['passed'] and runtime.state.phase == 'completed' and intact and error is None,
        grade=result_grade, error=error, phase=runtime.state.phase, stop_reason=runtime.state.stop_reason,
        usage=usage, ledger_usage=dict(known), usage_matches=usage == dict(known),
        model_calls=len(ledger.requests), http_attempts=len(attempts),
        http_without_total_usage=sum('total_tokens' not in x.get('usage',{}) for x in attempts),
        response_models=dict(Counter(x.get('response_model','') for x in attempts)),
        workers_started=len(child), workers_completed=sum(x['phase']=='completed' for x in child),
        explicit_assignments=assignments, parent_input_tokens=sum(parent_inputs),
        parent_peak_input_tokens=max(parent_inputs,default=0),
        parent_message_characters=sum(x['message_characters'] for x in parent_requests),
        parent_read_calls=len(parent_paths), parent_repeated_child_read_calls=sum(x in child_paths for x in parent_paths),
        child_local_allocation=list(local_allocation),
        tool_calls=len(tools), coordination_calls=sum(x['name'].startswith('worker_') for x in tools),
        workspace_unchanged=intact, source_unchanged=identity(commit)==frozen,
        final=runtime.state.final, runs=runs, tools=tools)
    save(output / 'result.json', runtime.evidence.redactor.value(result))
    print(json.dumps({k:result[k] for k in ['task','arm','repeat','passed','seconds','usage']},ensure_ascii=False),flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('preflight','freeze','run','episode'))
    parser.add_argument('--commit')
    parser.add_argument('--task')
    parser.add_argument('--arm', choices=ARMS)
    parser.add_argument('--repeat', type=int, choices=(1,2))
    args = parser.parse_args()
    tasks = bank()
    if args.mode == 'preflight':
        return preflight()
    if not args.commit:
        parser.error('--commit is required outside offline preflight')
    current = identity(args.commit)
    freeze = OUT / 'freeze.json'
    if args.mode == 'freeze':
        if freeze.exists() and json.loads(freeze.read_text()) != current:
            raise ValueError('freeze_already_exists')
        save(freeze,current)
        print('frozen',args.commit)
        return
    if not freeze.exists() or json.loads(freeze.read_text()) != current:
        raise ValueError('freeze_missing_or_changed')
    if args.mode == 'episode':
        task = next(t for t in tasks if t['id']==args.task)
        return asyncio.run(episode(task,args.arm,args.repeat,args.commit))
    for task in tasks:
        for repeat in (1,2):
            for arm in task['arm_orders'][repeat-1]:
                name=f"{task['id']}--{arm}--{repeat}"
                folder=OUT/'runs'/name
                if (folder/'result.json').exists():
                    print('existing',name,flush=True)
                    continue
                if folder.exists():
                    raise ValueError('incomplete_episode_requires_audit:'+name)
                log=OUT/'logs'/(name+'.txt')
                log.parent.mkdir(parents=True,exist_ok=True)
                with log.open('w') as stream:
                    subprocess.run([sys.executable,str(__file__),'episode','--commit',args.commit,
                                    '--task',task['id'],'--arm',arm,'--repeat',str(repeat)],cwd=REPO,
                                   env=dict(os.environ,PYTHONPATH=str(REPO/'src')),stdout=stream,stderr=subprocess.STDOUT,check=True)
                print('finished',name,flush=True)


if __name__ == '__main__':
    main()
