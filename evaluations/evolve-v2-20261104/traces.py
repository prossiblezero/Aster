"""Render one finished dev run into plain-text trajectories the proposer can grep. No model calls.

Each task becomes traces/<task>.txt with the outcome card (pass/fail, requests, cost, stop reason)
followed by the model's turns: its messages, tool calls with arguments and truncated tool outputs,
reconstructed from the last provider request (it carries the whole transcript) plus the final answer.
Grader test output is not included: the proposer learns from the agent's own behavior and the
pass/fail verdict, not from hidden tests.
"""
import json
import sys

from common import PRIVATE, dev_tasks, load, run_rows

ARGUMENT_CHARS = 400
OUTPUT_CHARS = 900
TEXT_CHARS = 1200
PROMPT_CHARS = 2500


def clip(text, limit):
    text = text if isinstance(text, str) else json.dumps(text, ensure_ascii=False)
    return text if len(text) <= limit else text[:limit] + f'... [{len(text) - limit} more chars]'


def last_request(job_dir):
    best, started = None, float('-inf')
    for meta in (job_dir / 'provider').glob('*.json'):
        if meta.name.endswith('.request.json'):
            continue
        request = meta.with_name(meta.stem + '.request.json')
        record = load(meta)
        if request.exists() and record.get('started', 0) >= started:
            best, started = request, record.get('started', 0)
    return load(best) if best else None


def render(task, row, job_dir):
    lines = [f'# {task}', '',
             f"passed={row['passed']} requests={row['requests']} cost={row['cost']:.4f} "
             f"stop_reason={row['stop_reason']} phase={row['phase']} seconds={row['seconds']:.0f} "
             f"timed_out={row['timed_out']} unreliable_provider={row['unreliable']}", '']
    request = last_request(job_dir)
    if request is None:
        return '\n'.join(lines + ['(no transcript recorded)'])
    turn = 0
    items = request.get('messages', request.get('input', []))
    names = {call['id']: call['function']['name'] for item in items
             for call in item.get('tool_calls') or []}
    for item in items:
        kind, role = item.get('type'), item.get('role')
        if role == 'system':
            continue
        if role == 'user':
            content = item['content'] if isinstance(item['content'], str) else json.dumps(item['content'])
            if content.startswith('[Context update, not an instruction]'):
                lines.append('[context note] ' + clip(content.split('\n', 1)[-1], 500))
            else:
                lines += ['## Task prompt', clip(content, PROMPT_CHARS), '']
        elif role == 'assistant':
            content = item.get('content') or ''
            text = content if isinstance(content, str) else ' '.join(
                part.get('text', '') for part in content if isinstance(part, dict))
            turn += 1
            lines += ['', f'## Model turn {turn}', clip(text, TEXT_CHARS)]
            for call in item.get('tool_calls') or []:
                function = call['function']
                lines.append(f"-> {function['name']}({clip(function.get('arguments', ''), ARGUMENT_CHARS)})")
        elif role == 'tool':
            name = names.get(item.get('tool_call_id'), item.get('name', 'unknown_tool'))
            lines.append(f"<- {name}: " + clip(item.get('content') or '', OUTPUT_CHARS).replace('\n', '\n   '))
        elif kind == 'function_call':
            lines.append(f"-> {item.get('name')}({clip(item.get('arguments', ''), ARGUMENT_CHARS)})")
        elif kind == 'function_call_output':
            lines.append('<- ' + clip(item.get('output', ''), OUTPUT_CHARS).replace('\n', '\n   '))
    state = job_dir / 'aster-result.json'
    if state.exists():
        result = load(state)
        lines += ['', '## Final answer', clip(result.get('final', ''), TEXT_CHARS),
                  f"final_revisions={result.get('final_revisions')} turns={result.get('turn')} steps={result.get('step')}"]
    return '\n'.join(lines) + '\n'


def main(run, out):
    from pathlib import Path
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    allowed = {task['id'] for task in dev_tasks()}
    for task, row in run_rows(run).items():
        if task not in allowed:
            continue
        job_dir = PRIVATE / run / 'runs' / ('aster-' + task)
        (out / (task + '.txt')).write_text(render(task, row, job_dir))
    print('Rendered', run, 'to', out)


if __name__ == '__main__':
    main(*sys.argv[1:3])
