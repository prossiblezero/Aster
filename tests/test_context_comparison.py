"""Before/after reports may not hide lost attempts or changed evaluation data."""

import copy
import json
from pathlib import Path

import pytest
from compare_context_runs import compare, index, markdown


def baseline():
    path = Path(__file__).resolve().parents[1] / "artifacts/benchmark-v5-robustness/report.json"
    report = json.loads(path.read_text())
    report["dataset_sha256"] = {"example": "unchanged"}
    return report


def test_report_keeps_primary_and_control_separate_and_preserves_failures():
    before = baseline()
    after = copy.deepcopy(before)
    for row in after["rows"]:
        if row["experiment"] == "context" and row["variant"] == "tight":
            row["passed"] = True
    report = compare(before, after)
    tight = report["groups"]["primary/tight"]
    assert tight["before"]["passed"] == 1 and tight["after"]["passed"] == 36
    assert tight["before"]["pass_at_1"] == pytest.approx(1 / 36)
    assert tight["before"]["pass_at_3"] == pytest.approx(1 / 12)
    assert tight["common_success"]["before"]["attempts"] == 1
    assert report["groups"]["loss_control/tight"]["after"]["attempts"] == 3
    assert len(report["failures"]["before"]) == 51
    assert "36 次尝试不是 Pass@3" in markdown(report)


@pytest.mark.parametrize("mutation", ["duplicate", "missing_attempt", "missing_task"])
def test_missing_or_duplicate_attempts_are_rejected(mutation):
    report = baseline()
    row = next(row for row in report["rows"] if row["experiment"] == "context")
    if mutation == "duplicate":
        report["rows"].append(row)
    elif mutation == "missing_attempt":
        report["rows"].remove(row)
    else:
        report["rows"] = [item for item in report["rows"] if item["task_id"] != row["task_id"]]
    with pytest.raises(ValueError, match="incomplete or duplicate"):
        index(report)


@pytest.mark.parametrize("field", ["settings", "dataset_sha256"])
def test_changed_experiment_is_rejected(field):
    before = baseline()
    after = copy.deepcopy(before)
    after[field]["changed"] = True
    with pytest.raises(ValueError):
        compare(before, after)


def only_balanced(report):
    selected = copy.deepcopy(report)
    selected["rows"] = [row for row in selected["rows"] if row["variant"] == "balanced"]
    selected["manifest"] = [row for row in selected["manifest"] if row["variant"] == "balanced"]
    selected["effective_context_budgets"] = {"balanced": 8192}
    return selected


def test_selected_condition_uses_full_task_set_and_only_declared_setting_removal():
    before = baseline()
    after = only_balanced(before)
    del after["settings"]["tool_output_tokens"]
    after["ignored_legacy_settings"] = {"tool_output_tokens": 1600}
    report = compare(before, after)
    assert report["groups"]["primary/balanced"]["after"]["attempts"] == 36
    assert report["compared_conditions"] == [{
        "input_budget": 6144, "before_variant": "balanced", "after_variant": "balanced",
    }]
    assert report["uncompared_conditions"]["before"] == {"full": 30720, "tight": 3584}
    assert report["setting_changes"][0]["setting"] == "tool_output_tokens"
    assert all(row["variant"] == "balanced" for row in report["failures"]["before"])
    del after["ignored_legacy_settings"]
    with pytest.raises(ValueError, match="explicitly recorded"):
        compare(before, after)


def test_comparison_rejects_smaller_task_manifest_instead_of_taking_intersection():
    before = baseline()
    after = only_balanced(before)
    task = after["rows"][0]["task_id"]
    for field in ("rows", "manifest"):
        after[field] = [row for row in after[field] if row["task_id"] != task]
    with pytest.raises(ValueError, match="same complete task set"):
        compare(before, after)


def test_budget_alias_matches_by_measured_input_budget_and_rejects_changed_budget():
    before = baseline()
    after = only_balanced(before)
    for field in ("rows", "manifest"):
        for row in after[field]:
            row["variant"] = "input_6144"
    after["input_budget_override"] = 6144
    after["effective_context_budgets"] = {"input_6144": 8192}
    assert compare(before, after)["compared_conditions"][0]["after_variant"] == "input_6144"
    after["effective_context_budgets"]["input_6144"] = 10240
    with pytest.raises(ValueError, match="explicit input token label"):
        compare(before, after)


def test_repeated_manifest_entries_are_rejected():
    report = only_balanced(baseline())
    report["manifest"].append(report["manifest"][0])
    with pytest.raises(ValueError, match="incomplete or duplicate"):
        index(report)
