"""Recovery fixtures exercise detection, false positives and persisted budgets."""

import json
from pathlib import Path

import pytest
from mechanism_support import run_case

from aster.recovery import StallDetector
from aster.schema import Outcome, ToolCall

DATASET = json.loads(
    (Path(__file__).resolve().parents[1] / "datasets" / "runtime_cases.json").read_text()
)
SCENARIOS = [
    pytest.param(dict(DATASET["defaults"], **case), variant, id=f"{case['id']}/{variant}")
    for case in DATASET["cases"]
    if case["suite"] in {"recovery", "recovery_controls", "resume"}
    for variant in case["variants"]
]


@pytest.mark.parametrize("case,variant", SCENARIOS)
async def test_runtime_recovery_contract(case, variant, tmp_path):
    result = await run_case(case, tmp_path, variant=variant)
    assert result["contract_passed"], result
    # The two arms intentionally have different expected behavior but a shared goal.
    expected = case["variants"][variant]["expected"]
    if expected == case["goal"]:
        assert result["goal_passed"], result
    reasons = case["variants"][variant].get("expected_stalls")
    if reasons is not None:
        events = sorted(
            (
                json.loads(line)
                for trace in tmp_path.glob(".aster/runs/*/trace.jsonl")
                for line in trace.read_text().splitlines()
            ),
            key=lambda event: (event["time"], event["seq"]),
        )
        assert [e["reason"] for e in events if e["event"] == "stall_detected"] == reasons


@pytest.mark.parametrize("case", DATASET["detector_contracts"], ids=lambda case: case["id"])
def test_detector_comparison_contract(case):
    detector = StallDetector()
    reasons = [
        detector.observe(
            ToolCall.model_validate(observation["call"]),
            Outcome.model_validate(observation["outcome"]),
        )
        for observation in case["observations"]
    ]
    assert reasons == case["expected_reasons"]
