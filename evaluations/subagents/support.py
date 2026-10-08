"""Explicit read-only subagent scenario experiment; no provider calls in preflight."""
# ruff: noqa: E402
from __future__ import annotations

import ast
import difflib
import hashlib
import importlib.metadata
import importlib.util
import json
import subprocess
import sys
import tarfile
import tempfile
from contextlib import contextmanager
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
DATA = REPO / 'datasets/subagent-light-20260930'
CACHE = REPO / 'artifacts/private/worker-code-v2/objects'
OUT = REPO / 'artifacts/private/subagent-light-20260930'
sys.path.insert(0, str(REPO / 'src'))
spec = importlib.util.spec_from_file_location('light_ledger', HERE / 'ledger.py')
ledger_driver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ledger_driver)
from aster.core.hooks import Hooks as Hooks
from aster.core.runtime import Runtime as Runtime
from aster.providers.client import ModelClient
from aster.schema import TaskSpec as TaskSpec

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

def settings(arm):
    return ledger_driver.config(max_workers=0, read_only=True,
                                memory_enabled=False, memory_evolution=False, auto_dream=False)

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
