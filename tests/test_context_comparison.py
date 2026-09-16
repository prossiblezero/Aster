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
