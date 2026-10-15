"""Offline turn accounting, using the v10 final-request transcript convention."""
import argparse
import collections
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'cache-probe-20261014'))
from action_audit import classify, requests  # noqa: E402

PREFIX = '[Context update, not an instruction]\nworkflow_state:\n'
MODEL_ITEMS = {'reasoning', 'message', 'function_call', 'custom_tool_call'}
FIELDS = ('turns', 'reread_after_own_edit', 'reread_only_turns', 'fresh_read_blocked',
          'search_calls', 'search_only_turns', 'turns_while_failing', 'completion_gates',
          'preexisting_declared')


def decoded(value):
    if isinstance(value, dict):
        return value
    try:
        return json.loads(value or '{}')
    except (ValueError, TypeError):
        return {}


def measure_items(items):
    counts = dict.fromkeys(FIELDS, 0)
    kinds = collections.Counter()
    outputs = {x['call_id']: decoded(x.get('output')) for x in items
               if x.get('type') == 'function_call_output'}
    edited, turn, failing = set(), [], False

    def collect():
        if not turn:
            return
        counts['turns'] += 1
        counts['turns_while_failing'] += failing
        labels, names, reread_paths = set(), [], []
        edited_before_turn = set(edited)
        for item in turn:
            if item.get('type') != 'function_call':
                continue
            name, args = item['name'], decoded(item.get('arguments'))
            labels.add(classify('aster', name, args))
            names.append(name)
            result = outputs.get(item['call_id'], {})
            path = args.get('path')
            if name in {'patch_file', 'write_file'}:
                if result.get('reason') == 'fresh_read_required':
                    counts['fresh_read_blocked'] += 1
                else:
                    # Preserve reread_audit.py's historical definition: all non-gate edits.
                    edited.add(path)
            elif name == 'read_file':
                reread_paths.append(path)
                if path in edited:
                    counts['reread_after_own_edit'] += 1
                    edited.discard(path)
            elif name == 'search':
                counts['search_calls'] += 1
            if name == 'run_tests':
                content = result.get('content', '')
                marker = 'Accepted as pre-existing: '
                if marker in content:
                    accepted, _ = json.JSONDecoder().raw_decode(content.split(marker, 1)[1])
                    counts['preexisting_declared'] += len(accepted)
        # Several ranges of the same edited file still make one reread-only turn.
        counts['reread_only_turns'] += (bool(names) and len(reread_paths) == len(names)
                                        and set(reread_paths) <= edited_before_turn)
        counts['search_only_turns'] += bool(names) and set(names) <= {'search', 'list_files'}
        kind = ('no_tool (answer/commentary)' if not labels else
                'discover_only' if labels == {'discover'} else
                'edit' if 'edit' in labels else 'test' if 'test' in labels else
                'diff/review' if labels <= {'review_diff', 'discover'} else 'shell_other')
        kinds[kind] += 1
        turn.clear()

    for item in items:
        if (item.get('type') or item.get('role')) in MODEL_ITEMS:
            turn.append(item)
            continue
        collect()
        content = item.get('content', '')
        if item.get('role') == 'user' and isinstance(content, str):
            if content.startswith(PREFIX):
                failing = bool(json.loads(content[len(PREFIX):]).get('verification_failures'))
            elif content.startswith('Completion requirements missing'):
                counts['completion_gates'] += 1
    collect()
    return {**counts, 'turn_kinds': dict(kinds)}


def measure(runs):
    tasks = {}
    for directory in sorted(runs.glob('aster-*/provider')):
        records = requests(directory)
        if not records:
            raise ValueError(f'No foreground transcript: {directory}')
        tasks[directory.parent.name[6:]] = measure_items(records[-1][1]['input'])
    if not tasks:
        raise ValueError(f'No Aster archives: {runs}')
    kinds = collections.Counter()
    for row in tasks.values():
        kinds.update(row['turn_kinds'])
    return {**{key: sum(row[key] for row in tasks.values()) for key in FIELDS},
            'turn_kinds': dict(kinds), 'tasks': tasks}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('runs', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    report = json.dumps(measure(args.runs), indent=2) + '\n'
    if args.output:
        args.output.write_text(report)
    print(report, end='')


if __name__ == '__main__':
    main()
