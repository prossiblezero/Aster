"""Positive/negative schedule-oracle fixtures only; no candidate or browser implementation."""

import importlib.util
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "study_grader", Path(__file__).with_name("study-grader.py")
)
GRADER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GRADER)


def fixture():
    courses = [
        {"name": "数学", "exam": "2099-06-03", "hours": "2.5"},
        {"name": "英语", "exam": "2099-06-04", "hours": "2"},
    ]
    rows = [
        {"日期": "2099-06-01", "课程": "数学", "小时": "1.5"},
        {"日期": "2099-06-02", "课程": "数学", "小时": "1 小时"},
        {"日期": "2099-06-02", "课程": "英语", "小时": "1"},
        {"日期": "2099-06-03", "课程": "英语", "小时": "1"},
    ]
    return courses, rows


def test_accepts_complete_feasible_schedule_without_fixed_order_or_splits():
    courses, rows = fixture()
    assert GRADER.verify_schedule(rows[::-1], courses, GRADER.START, "2") == 0
    rows[1]["小时"] = "0.5"
    rows.insert(2, {"日期": "2099-06-02", "课程": "数学", "小时": "0.5"})
    assert GRADER.verify_schedule(rows, courses, GRADER.START, "2") == 0


@pytest.mark.parametrize(
    "mutation",
    [
        "exam_day",
        "before_start",
        "daily_overflow",
        "course_overflow",
        "missing_hours",
        "unknown_course",
        "zero_hours",
        "negative_hours",
        "nan",
        "bad_date",
    ],
)
def test_rejects_invalid_plans(mutation):
    courses, rows = fixture()
    if mutation == "exam_day":
        rows[0]["日期"] = "2099-06-03"
    elif mutation == "before_start":
        rows[0]["日期"] = "2099-05-31"
    elif mutation == "daily_overflow":
        rows[1]["日期"] = "2099-06-01"
    elif mutation == "course_overflow":
        rows.append({"日期": "2099-06-03", "课程": "英语", "小时": "0.5"})
    elif mutation == "missing_hours":
        rows.pop()
    elif mutation == "unknown_course":
        rows[0]["课程"] = "已删除课程"
    elif mutation in {"zero_hours", "negative_hours", "nan"}:
        rows[0]["小时"] = {"zero_hours": "0", "negative_hours": "-1", "nan": "NaN"}[mutation]
    else:
        rows[0]["日期"] = "2099-02-30"
    with pytest.raises((AssertionError, ValueError)):
        GRADER.verify_schedule(rows, courses, GRADER.START, "2")


def test_prefix_shortage_detects_early_overload_even_with_later_spare_capacity():
    courses = [
        {"name": "近期", "exam": "2099-06-03", "hours": "7"},
        {"name": "后续", "exam": "2099-06-05", "hours": "1"},
    ]
    rows = [
        {"日期": "2099-06-01", "课程": "近期", "小时": "2"},
        {"日期": "2099-06-02", "课程": "近期", "小时": "2"},
        {"日期": "2099-06-03", "课程": "后续", "小时": "1"},
    ]
    assert GRADER.required_shortage(courses, GRADER.START, "2") == 3
    assert GRADER.verify_schedule(rows, courses, GRADER.START, "2") == 3
    with pytest.raises(AssertionError, match="Missing feasible"):
        GRADER.verify_schedule(rows[:-1], courses, GRADER.START, "2")
    invalid = deepcopy(rows)
    invalid.append({"日期": "2099-06-03", "课程": "近期", "小时": "1"})
    with pytest.raises(AssertionError, match="allowed dates"):
        GRADER.verify_schedule(invalid, courses, GRADER.START, "2")


def test_past_and_same_day_exams_have_no_capacity():
    courses = [
        {"name": "过期", "exam": "2099-05-30", "hours": "1.5"},
        {"name": "当天", "exam": GRADER.START, "hours": "2"},
    ]
    assert GRADER.verify_schedule([], courses, GRADER.START, "10") == Decimal("3.5")
    assert GRADER.verify_schedule([], [], GRADER.START, "2") == 0


def test_same_deadline_demand_is_aggregated_and_fractional_hours_are_exact():
    courses = [
        {"name": "甲", "exam": "2099-06-02", "hours": "0.1"},
        {"name": "乙", "exam": "2099-06-02", "hours": "0.2"},
    ]
    rows = [
        {"日期": GRADER.START, "课程": "甲", "小时": "0.1"},
        {"日期": GRADER.START, "课程": "乙", "小时": "0.2"},
    ]
    assert GRADER.verify_schedule(rows, courses, GRADER.START, "0.3") == 0
    assert GRADER.required_shortage(courses, GRADER.START, "0.2") == Decimal("0.1")
