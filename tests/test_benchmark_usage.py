"""Unknown and partial provider usage stays visible through benchmark consumers."""

from benchmark import markdown as benchmark_markdown
from benchmark import summarize
from benchmark_robustness import markdown as robustness_markdown
from benchmark_robustness import paired_summary
from benchmark_robustness import summarize as summarize_robustness
from benchmark_support import metrics
from compare_benchmarks import metrics as comparison_metrics
from compare_benchmarks import paired

from aster.config import Settings
from aster.runtime import Runtime
from aster.schema import TaskSpec


def test_usage_metrics_keep_unknown_zero_and_per_metric_coverage(tmp_path):
    runtime = Runtime(tmp_path, Settings())
    runtime.start(TaskSpec(prompt="inspect"))
    runtime.state.turn = 3
    runtime.state.usage = {
        "usage_requests": 2,
        "input_tokens": 12,
        "input_tokens_requests": 2,
        "output_tokens": 3,
        "output_tokens_requests": 1,
        "total_tokens": 15,
        "total_tokens_requests": 1,
        "cache_read_input_tokens": 0,
        "cache_read_input_tokens_requests": 1,
    }
    row = metrics(runtime, 0)
    assert row["usage"] == runtime.state.usage
    assert row["cache_read_input_tokens"] == 0
    assert row["reasoning_tokens"] is None
    assert row["usage_coverage"]["total_tokens"] == {
        "reported_requests": 1,
        "model_requests": 3,
        "complete": False,
    }
    assert row["usage_coverage"]["reasoning_tokens"]["reported_requests"] == 0
    runtime.state.usage = {"input_tokens": 12}
    assert metrics(runtime, 0)["total_tokens"] is None
    runtime.state.usage = {
        "usage_requests": 2,
        "input_tokens": 12,
        "input_tokens_requests": 1,
        "output_tokens": 3,
        "output_tokens_requests": 1,
    }
    assert metrics(runtime, 0)["total_tokens"] is None
    runtime.state.usage = {"input_tokens": 12, "output_tokens": 3, "total_tokens": 0}
    assert metrics(runtime, 0)["total_tokens"] == 0
    runtime.state.usage = {}
    assert metrics(runtime, 0)["total_tokens"] is None


def usage_row(tokens, *, variant="core", complete=False):
    return {
        "task_id": "example",
        "suite": "repository",
        "experiment": "context",
        "category": "example",
        "variant": variant,
        "attempt": 1,
        "passed": True,
        "total_tokens": tokens,
        "usage_coverage": {
            "total_tokens": {
                "reported_requests": int(tokens is not None),
                "model_requests": 1 if complete else 2,
                "complete": complete,
            }
        },
        "steps": 1,
        "autonomous_steps": 1,
        "seconds": 1,
        "read_calls": 0,
        "evidence_read_calls": 0,
        "stop_reason": "completed",
    }


def test_consumers_keep_partial_totals_without_claiming_complete_costs():
    rows = [usage_row(15), usage_row(None)]
    rows[1]["attempt"] = 2
    group = summarize(rows)["repository/core"]
    assert group["mean_tokens_all"] is None
    coverage = group["usage_summary"]["total_tokens"]
    assert coverage["reported_tokens"] == 15
    assert coverage["reported_requests"] == 1 and coverage["model_requests"] == 4
    assert not coverage["complete"]
    compared = comparison_metrics(rows)
    assert compared["total_tokens"] == 15 and compared["mean_tokens"] is None
    assert compared["usage_summary"] == group["usage_summary"]
    report = {
        "mode": "real",
        "source": {"content_sha256": "test"},
        "summary": summarize(rows),
        "rows": rows,
    }
    rendered = benchmark_markdown(report)
    assert "—" in rendered and "已报告" in rendered and "1/4" in rendered
    assert paired([usage_row(15)], [usage_row(10)])["common_success"]["token_reduction"] is None
    assert paired([usage_row(15)], [usage_row(None)])["common_success"]["token_reduction"] is None
    reduction = paired([usage_row(20, complete=True)], [usage_row(10, complete=True)])
    assert reduction["common_success"]["token_reduction"] == 0.5
    unknown = usage_row(None)
    zero = usage_row(0, complete=True)
    unknown["passed"] = zero["passed"] = False
    assert comparison_metrics([unknown, zero])["failed_without_reported_tokens"] == 1


def test_robustness_consumer_and_historical_rows_keep_coverage_unknown():
    rows = [usage_row(15, variant="full"), usage_row(None, variant="tight")]
    summary = summarize_robustness(rows)
    pairs = paired_summary(rows)
    assert summary["context/example/tight"]["mean_tokens_all"] is None
    assert pairs["context/example/full/tight"]["common_success_total_tokens"] == [15, None]
    rendered = robustness_markdown({"repeats": 1, "summary": summary, "pairs": pairs, "rows": rows})
    assert "—" in rendered and "已报告" in rendered
    historical = usage_row(20)
    historical.pop("usage_coverage")
    group = comparison_metrics([historical])
    assert group["total_tokens"] == 20
    assert group["usage_summary"]["total_tokens"]["reported_requests"] is None
    assert not group["usage_summary"]["total_tokens"]["complete"]
