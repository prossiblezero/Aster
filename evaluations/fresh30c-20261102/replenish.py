"""Replace tasks whose reference fails preflight with the next ranked candidate. No model calls.

Rule (outcome-independent of agents): keep the composition; a broken task (reference does not
pass) is replaced by the next candidate in the same seeded ranking that passes preflight.

Terminal tasks: `replenish.py terminal START STOP` first records candidates whose reference setup
died on a missing network (apt/pip, no preflight.json written) as not ready, then provisions the
next ranked candidates [START:STOP] and runs preflight, which keeps the first two ready in rank order.
"""
import ast
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import preflight as fresh  # noqa: E402
from prepare import G50, PRIVATE, load, save  # noqa: E402


def terminal(start, stop):
    import provision
    for folder in (PRIVATE / 'preflight').glob('terminal-*'):
        if not (folder / 'preflight.json').exists() and (folder / 'reference.log').exists():
            save(folder / 'preflight.json', {'id': folder.name, 'ready': False,
                                             'reason': 'reference setup failed without network (assertion in setup)'})
    provision.main(start, stop)
    fresh.main()
    print(json.dumps(load(PRIVATE / 'tasks.json') and [t['id'] for t in load(PRIVATE / 'tasks.json')]))


def main():
    if sys.argv[1:2] == ['terminal']:
        return terminal(int(sys.argv[2]), int(sys.argv[3]))
    selection = load(HERE.parents[1] / 'datasets' / HERE.name / 'selection.json')
    tasks = load(PRIVATE / 'tasks.provisioned.json')
    bad = [t for t in tasks if t['kind'] == 'library'
           and not load(PRIVATE / 'preflight' / t['id'] / 'preflight.json')['ready']]
    bigrows = {r['task_id']: r for r in load(G50 / 'sources/bigcodebench.json')}
    used = {t.get('source_id') for t in tasks}
    queue = [i for i in selection['bigcodebench']['ranked'][10:] if i not in used]
    template = next(t for t in tasks if t['kind'] == 'library')
    added = []
    for _ in bad:
        row = bigrows[queue.pop(0)]
        name = 'bigcodebench-' + row['task_id'].split('/')[1]
        folder = PRIVATE / 'sources/library-tasks' / name
        folder.mkdir(parents=True)
        save(folder / 'record.json', row)
        (folder / 'solution.py').write_text(row['code_prompt'] + '    pass\n')
        (folder / 'reference.py').write_text(row['complete_prompt'] + row['canonical_solution'])
        (folder / 'test_solution.py').write_text(row['test'])
        task = {**template, 'id': name, 'source_id': row['task_id'], 'task_name': name,
                'prompt': row['instruct_prompt'] + '\nImplement task_func in solution.py.\n' + row['complete_prompt'],
                'libraries': ast.literal_eval(row['libs']), 'replaces': [b['id'] for b in bad]}
        import hashlib
        task['prompt_sha256'] = hashlib.sha256(task['prompt'].encode()).hexdigest()
        tasks.append(task)
        added.append(name)
    save(PRIVATE / 'tasks.provisioned.json', tasks)
    selection['bigcodebench']['replacements'] = {'broken': [b['id'] for b in bad], 'added': added}
    save(HERE.parents[1] / 'datasets' / HERE.name / 'selection.json', selection)
    print('Added', added, 'for', [b['id'] for b in bad])
    fresh.main()  # preflight only runs tasks without a stored result, then rewrites tasks.json


if __name__ == '__main__':
    main()
