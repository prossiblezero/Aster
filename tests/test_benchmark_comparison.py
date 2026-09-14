"""Comparison statistics must preserve failed and unmatched attempts."""

import copy
import json
from pathlib import Path

import pytest
from compare_benchmarks import build_report, compare_mode, index_rows, markdown

REPORTS = Path(__file__).resolve().parents[1] / "artifacts"


def baseline(mode):
    return json.loads((REPORTS / f"benchmark-v2-{mode}" / "report.json").read_text())


def test_cost_subset_does_not_replace_all_attempts_or_mix_variants():
    before = baseline("real")
    after = copy.deepcopy(before)
    for row in after["rows"]:
        if row["suite"] == "repository" and row["variant"] == "core":
            row["passed"] = True
    result = compare_mode(before, after)
    core, all_tools = result["repository/core"], result["repository/all"]
    assert core["before"]["passed"] == 2 and core["after"]["passed"] == 5
    assert core["common_success"]["before"]["attempts"] == 2
    assert len(core["attempt_pairs"]) == 5 and len(core["fixed"]) == 3
    assert all_tools["after"]["passed"] == 2
    assert core["before"]["attempts"] == core["after"]["attempts"] == 5


def test_missing_and_duplicate_attempts_are_not_silently_dropped():
    before = baseline("real")
    missing = copy.deepcopy(before)
    missing["rows"].pop()
    with pytest.raises(ValueError, match="incomplete"):
        index_rows(missing)
    duplicate = copy.deepcopy(before)
    duplicate["rows"].append(duplicate["rows"][0])
    with pytest.raises(ValueError, match="duplicate"):
        index_rows(duplicate)


def test_complete_report_renders_without_claiming_failure_filter_is_full_sample():
    scripted, real = baseline("scripted"), baseline("real")
    report = build_report(scripted, real, scripted, real)
    rendered = markdown(report)
    assert "全部尝试" in rendered and "补充" in rendered
    assert report["real"]["memory/memory_on"]["before"]["attempts"] == 12
    assert (
        report["real_variant_comparisons"]["after"]["memory/memory_off_to_memory_on"][
            "common_success"
        ]["before"]["attempts"]
        == 10
    )
    assert report["context"]["before"]["within_budget"] == 0
    assert len(report["failures"]["before_real"]) == 8
