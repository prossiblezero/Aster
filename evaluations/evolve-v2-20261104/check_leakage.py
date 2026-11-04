"""Synthetic-only check: an inherited tool name is not newly introduced leakage."""
import json
from unittest.mock import patch

import final


def scan(text):
    def git_output(args, **kwargs):
        if args[1] == 'show':
            return json.dumps({'tools': {'run_tests': 'Run pytest or SymPy.'}})
        assert args[1] == 'diff'
        return '+    "run_tests": ' + json.dumps(text) + '\n'

    fake_pool = [{'id': 'synthetic-unseen-exercise-999', 'repo': 'pytest-dev/pytest'},
                 {'id': 'synthetic-unseen-task-888', 'repo': 'sympy/sympy'}]
    with patch.object(final.subprocess, 'check_output', side_effect=git_output), \
         patch.object(final, 'load', return_value=fake_pool):
        return final.leakage('synthetic-candidate')


def main():
    inherited = scan('Run pytest or SymPy. Prefer focused tests.')
    assert inherited['passed'] and inherited['matches'] == [], inherited
    introduced = scan('Run pytest or SymPy for synthetic-unseen-exercise-999.')
    assert not introduced['passed'] and introduced['matches'] == ['synthetic-unseen-exercise-999']
    print('PASS: inherited identifiers ignored; newly introduced synthetic task identifier detected; no heldout files read')


if __name__ == '__main__':
    main()
