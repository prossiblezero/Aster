"""Independent public storage assertions accept reordering and reject undo defects."""

import importlib.util
from copy import deepcopy
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "maintenance_grader", Path(__file__).with_name("maintenance-grader.py")
)
GRADER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GRADER)


def test_complete_restore_accepts_equivalent_order():
    expected = [GRADER.BASE.task("one", completed=True), GRADER.BASE.task("two")]
    GRADER.assert_tasks(GRADER.BASE.package(expected[::-1]), expected)


@pytest.mark.parametrize(
    "defect",
    [
        "duplicate",
        "changed_id",
        "changed_status",
        "lost_sibling",
        "lost_notes",
        "wrong_title",
        "wrong_course",
        "wrong_date",
    ],
)
def test_undo_defects_are_rejected(defect):
    expected = [GRADER.BASE.task("one", completed=True), GRADER.BASE.task("two")]
    actual = deepcopy(expected)
    if defect == "duplicate":
        actual.append(deepcopy(actual[0]))
    elif defect == "lost_sibling":
        actual.pop()
    else:
        key, value = {
            "changed_id": ("id", "new-id"),
            "changed_status": ("completed", False),
            "lost_notes": ("notes", ""),
            "wrong_title": ("title", "changed"),
            "wrong_course": ("course", "changed"),
            "wrong_date": ("due", "2099-10-09T23:59"),
        }[defect]
        actual[0][key] = value
    with pytest.raises(AssertionError):
        GRADER.assert_tasks(GRADER.BASE.package(actual), expected)
