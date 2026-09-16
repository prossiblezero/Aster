"""Check experimental treatment and measurement, not model benchmark scores."""

import json

import benchmark_robustness
import pytest
from benchmark_robustness import (
    ObservedModel,
    action_key,
    initialize,
    load_cases,
    paired_summary,
    read_data,
    run_attempt,
    runtime_settings,
    selected_protocol,
    summarize,
)
from benchmark_support import ScriptedPolicy

from aster.config import Settings
from aster.schema import ModelReply
from aster.tools import SCHEMAS
from aster.workspace import Workspace


async def test_context_treatment_changes_only_budget_and_preserves_archived_evidence(tmp_path):
    protocol = read_data("robustness_protocol.json")
    settings = Settings(**runtime_settings(protocol))
    case = next(case for case in load_cases("context") if case["category"] == "loss_control")
    runtimes, setups = [], []
    for variant in ("full", "tight"):
        runtime, setup = await initialize(case, tmp_path / variant, settings, variant, protocol)
        runtimes.append(runtime)
        setups.append(setup)
    assert setups[0]["workspace_sha256"] == setups[1]["workspace_sha256"]
    full, tight = [setup["untrimmed"].copy() for setup in setups]
    full_tokens, tight_tokens = full.pop("estimated_tokens"), tight.pop("estimated_tokens")
    assert full == tight
    # Archive IDs bind to distinct run IDs; hex tokenization can change measured size
    # slightly even though both conditions contain exactly the same source observations.
    assert abs(full_tokens - tight_tokens) / full_tokens < 0.01
    archives = []
    for runtime in runtimes:
        records = [
            json.loads(path.read_text())
            for path in runtime.evidence.path("runs", runtime.state.run_id, "observations").glob("*.json")
        ]
        archives.append({(record["call_id"], record["tool"]): record["content"] for record in records})
    assert archives[0] == archives[1]
    assert set(archives[0].values()) == {
        message["content"] for message in [*case["history"], *read_data(protocol["history_file"])["messages"]]
        if message["role"] == "tool"
    }
    assert setups[0]["selected"]["removed_messages"] == 0
    assert setups[1]["selected"]["removed_messages"] > 0
    assert setups[1]["selected"]["estimated_tokens"] <= 3584
    configs = [runtime.settings.identity() for runtime in runtimes]
    for config in configs:
        config.pop("context_tokens")
    assert configs[0] == configs[1]
    assert all(not runtime.state.working_memory for runtime in runtimes)
    for runtime in runtimes:
        await runtime.workers.close()


async def test_silent_continuation_suppresses_only_feedback_and_preserves_seed(tmp_path):
    protocol = read_data("robustness_protocol.json")
    case = load_cases("recovery")[0]
    states = []
    for variant in ("feedback", "silent", "stop"):
        runtime, setup = await initialize(
            case, tmp_path / variant, Settings(**runtime_settings(protocol)), variant, protocol
        )
        rendered = json.dumps(
            [runtime.state.messages, runtime._structured_history, runtime._pending_messages]
        )
        assert ("Recovery required (" in rendered) == (variant == "feedback")
        assert runtime.state.terminal == (variant == "stop")
        assert setup["trigger"] == case["expected_trigger"]
        assert setup["seed_steps"] == len(case["seed_actions"])
        states.append(
            (runtime._recovery_used, runtime.state.working_memory, runtime.workspace.snapshot())
        )
        await runtime.workers.close()
    assert states[0] == states[1]
    assert states[0][0] == 1 and states[2][0] == 0


async def test_request_observer_forwards_unchanged_and_matches_escaped_evidence(tmp_path):
    protocol = read_data("robustness_protocol.json")
    runtime, _ = await initialize(
        load_cases("context")[0],
        tmp_path / "workspace",
        Settings(**runtime_settings(protocol)),
        "full",
        protocol,
    )
    messages = [{"role": "user", "content": 'Keep "quotes"\nand unicode 中文'}]
    definitions = runtime.tool_definitions()

    class Probe:
        async def complete(self, actual_messages, actual_tools):
            assert actual_messages is messages and actual_tools is definitions
            return ModelReply(text="observed")

    observer = ObservedModel(
        runtime, Probe(), tmp_path / "requests", ['"quotes"\nand unicode 中文']
    )
    assert (await observer.complete(messages, definitions)).text == "observed"
    assert observer.requests[0]["evidence_present"] == [True]
    stored = json.loads((tmp_path / "requests" / "request-001.json").read_text())
    assert stored["payload"]["input"] == messages
    await runtime.workers.close()


async def test_completed_run_archives_real_requests_and_independent_grade(tmp_path):
    protocol = read_data("robustness_protocol.json")
    case = load_cases("context")[0]
    result = await run_attempt(
        case,
        tmp_path / "workspace",
        tmp_path / "archive",
        Settings(**runtime_settings(protocol)),
        "tight",
        protocol,
        model=ScriptedPolicy(case["actions"]),
    )
    assert result["passed"], result
    assert result["history_compactions"] > 0 and result["all_within_budget"]
    assert result["model_requests"] == len(case["actions"])
    assert result["first_request"]["evidence_present"] == [False]
    assert result["verifier"]["tests"]["passed"] == 2
    assert (tmp_path / "archive" / "patch.diff").read_text()
    assert not list((tmp_path / "archive" / "candidate").glob("__evaluation*"))


def test_summary_keeps_failures_and_compares_only_matching_successful_pairs():
    rows = []
    for attempt, successes in enumerate(((True, True), (False, True), (False, False)), 1):
        for variant, passed in zip(("full", "tight"), successes, strict=True):
            rows.append(
                {
                    "experiment": "context",
                    "category": "example",
                    "task_id": "a",
                    "attempt": attempt,
                    "variant": variant,
                    "passed": passed,
                    "total_tokens": 100 if variant == "full" else 50,
                    "autonomous_steps": 4,
                    "seconds": 2,
                    "evidence_read_calls": 1,
                }
            )
    assert summarize(rows)["context/example/full"]["attempts"] == 3
    pair = paired_summary(rows)["context/example/full/tight"]
    assert (pair["both_pass"], pair["left_only"], pair["right_only"], pair["neither"]) == (
        1,
        0,
        1,
        1,
    )
    assert pair["common_success_total_tokens"] == [100, 50]


def test_action_changes_ignore_equivalent_defaults_and_path_spelling(tmp_path):
    (tmp_path / "a.py").write_text("pass\n")
    workspace = Workspace(tmp_path)
    implicit = {"name": "read_file", "arguments": {"path": "a.py"}}
    explicit = {
        "name": "read_file",
        "arguments": SCHEMAS["read_file"][0].model_validate({"path": "./a.py"}).model_dump(),
    }
    assert action_key(implicit, workspace) == action_key(explicit, workspace)
    assert action_key({"name": "read_file", "arguments": {"path": "../outside"}}, workspace) is None


async def test_grader_exception_preserves_completed_run_usage_and_artifacts(tmp_path, monkeypatch):
    async def broken_grader(*_):
        raise OSError("grader unavailable")

    monkeypatch.setattr(benchmark_robustness, "external_check", broken_grader)
    case = load_cases("context")[0]
    protocol = read_data("robustness_protocol.json")
    row = await run_attempt(
        case,
        tmp_path / "workspace",
        tmp_path / "archive",
        Settings(**runtime_settings(protocol)),
        "tight",
        protocol,
        model=ScriptedPolicy(case["actions"]),
    )
    assert not row["passed"] and not row["checks"]["independent_tests"]
    assert row["autonomous_steps"] > 0 and row["model_requests"] > 0
    assert row["stop_reason"] != "evaluation_setup_error"
    assert row["trace_path"] and "grader unavailable" in row["verifier"]["grader_reason"]


def test_condition_selection_preserves_original_protocol_and_labels_input_overrides():
    protocol = read_data("robustness_protocol.json")
    original = json.dumps(protocol, sort_keys=True)
    assert selected_protocol(protocol, "balanced")["context_budgets"] == {"balanced": 8192}
    assert selected_protocol(protocol, input_budget=8192)["context_budgets"] == {"input_8192": 10240}
    assert json.dumps(protocol, sort_keys=True) == original
    assert "tool_output_tokens" not in runtime_settings(protocol)
    assert protocol["settings"]["tool_output_tokens"] == 1600


@pytest.mark.parametrize("arguments", [
    ["--variant", "balanced"],
    ["--suite", "recovery", "--input-budget", "8192"],
    ["--suite", "context", "--variant", "balanced", "--input-budget", "8192"],
])
def test_context_condition_options_cannot_silently_change_other_experiments(monkeypatch, arguments):
    monkeypatch.setattr("sys.argv", ["benchmark_robustness.py", *arguments])
    with pytest.raises(SystemExit, match="2"):
        benchmark_robustness.main()
