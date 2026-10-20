"""Requirements guessed from a request: an edit is demanded only when one is clearly asked for.

Wrongly demanding an edit is the costly direction: the completion check would push the agent to
change code the user only asked about. Negations count only when they forbid touching the
workspace itself; constraints on the solution ("without changing existing values") do not make
a request read-only. Every prompt here is a real phrasing class seen in use or in evaluations.
"""

import pytest

from aster.core.verifier import Verifier

pytestmark = pytest.mark.core

QUESTIONS = [
    "Explain how the dispatch table in router.py works.",  # "patch" inside a word
    "Explain the suffix array implementation.",  # "fix" inside a word; a noun, not a request
    "Explain the pytest fixture in conftest.py.",
    "Explain why the cache does not update.",  # a negated verb is not a request
    "解释一下这个函数是怎么实现的",  # "how was it implemented" is a question
]

READ_ONLY = [
    "Inspect config.txt and tell me the mode. Do not modify files.",
    "Review src/app.py without modifying any files.",
    "Explain the bug; don't change anything, just tell me.",
    "Make no changes. Explain why the test fails.",
    "Read-only: summarize how the parser works.",
    "请分析这个模块，不要修改。",
    "不要对代码做任何修改，只解释原因",
    "无需修改代码即可回答：这个参数是什么意思",
    "只读分析这个仓库",
]

EDITS = [
    # (request, verification expected)
    ("Calculate the cumulative sum without changing existing values to NaN. "
     "Implement task_func in solution.py.", False),  # BigCodeBench/966, inferred read-only before
    ("Fix the dtype handling for transformers that do not modify the input values.", False),  # sklearn-25102
    ("Write a function that sorts a list without modifying it.", False),
    ("Implement task_func; do not modify the input DataFrame. Add tests.", True),
    ("Fix the bug without changing the public API and run the tests.", True),
    ("Fix the parser. Do not modify files other than parser.py.", False),
    ("Fix the flaky test without touching the public API.", True),
    ("Why does it not update? Fix it and run pytest.", True),
    ("Rewrite the loader to stream files.", False),
    ("实现这个函数，不要修改原始数据，并写测试", True),
    ("修复这个 bug，不要修改测试文件", True),
]


@pytest.mark.parametrize("prompt", QUESTIONS + READ_ONLY)
def test_questions_and_read_only_requests_never_demand_an_edit(prompt):
    inferred = Verifier.infer(prompt)
    assert not inferred["require_edit"] and not inferred["require_verification"]


@pytest.mark.parametrize("prompt,verification", EDITS)
def test_edit_requests_with_solution_constraints_still_demand_an_edit(prompt, verification):
    inferred = Verifier.infer(prompt)
    assert inferred["require_edit"] and inferred["require_observation"]
    assert inferred["require_verification"] == verification


def test_test_keywords_must_be_words():
    output_format = 'Implement recover.py. The output has the format [{"word": "testwordXY", "value": 1}].'
    assert Verifier.infer(output_format)["require_verification"] is False  # sqlite-db-truncate
    assert Verifier.infer("Update the changelog for the latest release.")["require_verification"] is False
    assert Verifier.infer("Fix the bug in parser.py; tests live in test_parser.py.")["require_verification"]
