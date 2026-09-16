"""Recompute the fixed development budget selection and its fresh-attempt confirmation."""

import argparse
import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean

OUTPUT = Path(__file__).resolve().parent
PROJECT = OUTPUT.parents[1]
ARTIFACTS = PROJECT / "artifacts"


def read(path):
    return json.loads(path.read_text())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pass_at_k(rows, k):
    tasks = defaultdict(list)
    for row in rows:
        tasks[row["task_id"]].append(row["passed"])
    if not tasks or any(len(values) < k for values in tasks.values()):
        return None
    return mean(1 - math.comb(len(v) - sum(v), k) / math.comb(len(v), k) for v in tasks.values())


def statistics(rows):
    measurements = [
        row
        for row in rows
        if row.get("first_request")
        and row.get("setup", {}).get("untrimmed", {}).get("estimated_tokens", 0) > 0
    ]
    return {
        "tasks": len({row["task_id"] for row in rows}),
        "attempts": len(rows),
        "completed": sum(row["passed"] for row in rows),
        "pass_at_1": pass_at_k(rows, 1),
        "pass_at_3": pass_at_k(rows, 3),
        "hidden_tests_passed": sum(row.get("verifier", {}).get("passed", False) for row in rows),
        "stop_reasons": dict(Counter(row["stop_reason"] for row in rows)),
        "first_request_measurements": len(measurements),
        "mean_untrimmed_input_tokens": mean(
            row["setup"]["untrimmed"]["estimated_tokens"] for row in measurements
        )
        if measurements
        else None,
        "mean_first_request_input_tokens": mean(
            row["first_request"]["estimated_tokens"] for row in measurements
        )
        if measurements
        else None,
        "mean_first_request_compression": mean(
            1
            - row["first_request"]["estimated_tokens"]
            / row["setup"]["untrimmed"]["estimated_tokens"]
            for row in measurements
        )
        if measurements
        else None,
        "provider_input_tokens": sum(row.get("input_tokens", 0) for row in rows),
        "provider_output_tokens": sum(row.get("output_tokens", 0) for row in rows),
        "provider_total_tokens": sum(row["total_tokens"] for row in rows),
        "seconds_serial": sum(row["seconds"] for row in rows),
        "model_requests": sum(row.get("model_requests", 0) for row in rows),
        "observation_read_calls": sum(row.get("observation_read_calls", 0) for row in rows),
    }


def summarize(path, baseline, *, budget, repeats, include_control):
    report = read(path)
    identities = read(OUTPUT / "dataset-identity.json")
    categories = {
        row["task_id"]: row["category"]
        for row in baseline["rows"]
        if row["experiment"] == "context"
    }
    tasks = {
        task
        for task, category in categories.items()
        if include_control or category != "loss_control"
    }
    rows = report["rows"]
    variants = {row["variant"] for row in report["manifest"]}
    assert len(variants) == 1 and report["repeats"] == repeats
    expected = {
        (task, variant, attempt)
        for task in tasks
        for variant in variants
        for attempt in range(1, repeats + 1)
    }

    def key(row):
        return row["task_id"], row["variant"], row["attempt"]

    assert len(rows) == len(expected) and set(map(key, rows)) == expected
    assert (
        len(report["manifest"]) == len(expected) and set(map(key, report["manifest"])) == expected
    )
    assert report["protocol"] == baseline["protocol"]
    assert baseline["dataset_sha256"] == identities["all"]
    assert report["dataset_sha256"] == identities["all" if include_control else "primary"]
    assert report["source"]["git_head"] == identities["code_commit"]
    assert all(
        row["experiment"] == "context" and row["category"] == categories[row["task_id"]]
        for row in rows
    )
    configured = report["effective_context_budgets"]
    variant = next(iter(variants))
    if variant in report["protocol"]["context_budgets"]:
        assert configured[variant] == report["protocol"]["context_budgets"][variant]
        assert report.get("input_budget_override") is None
    else:
        assert variant == f"input_{budget}" and report.get("input_budget_override") == budget
    assert all(
        configured[variant] - report["settings"]["output_tokens"] == budget for variant in variants
    )
    assert all(
        row.get("setup", {}).get("selected", {}).get("budget", budget) == budget for row in rows
    )
    batch_ids = {row["batch_id"] for row in rows}
    assert len(batch_ids) == 1
    return {
        "path": str(path.relative_to(PROJECT)),
        "sha256": digest(path),
        "source": report["source"],
        "settings": report["settings"],
        "created_at": report["created_at"],
        "batch_id": next(iter(batch_ids)),
        "input_budget": budget,
        "variants": sorted(variants),
        "manifest_complete": True,
        "primary": statistics([row for row in rows if row["category"] != "loss_control"]),
        "loss_control": statistics([row for row in rows if row["category"] == "loss_control"]),
        "all_attempts": [
            {
                name: row.get(name)
                for name in (
                    "task_id",
                    "variant",
                    "attempt",
                    "category",
                    "passed",
                    "stop_reason",
                    "failure",
                    "total_tokens",
                    "trace_path",
                )
            }
            for row in rows
        ],
    }


def qualifies(summary, completed, attempts, compression):
    measured = summary["primary"]
    return (
        measured["attempts"] == attempts
        and measured["completed"] >= completed
        and measured["first_request_measurements"] == attempts
        and measured["mean_first_request_compression"] >= compression
    )


def markdown(report):
    def number(value, percentage=False):
        return "—" if value is None else f"{value:.2%}" if percentage else f"{value:,.2f}"

    summaries = [report["same_budget_regression"], *report["candidates"]]
    if "confirmation" in report:
        summaries.append(report["confirmation"])
    lines = [
        "# 固定预算选择与确认",
        "",
        "原题已经用于开发；新批次是同题重复确认，不是未见测试集。全部失败保留，不挑成功样本。",
        f"选定输入预算：{report['selected_input_budget']}；选择规则与数据哈希见 JSON。",
        "",
        "| 阶段 / 输入预算 | 完成 | Pass@1 | Pass@3 | 隐藏功能通过 | 未压缩输入均值 | 实际首请求均值 | 平均首请求压缩 | 供应商总 token |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for item in summaries:
        row = item["primary"]
        lines.append(
            f"| {item['path']} / {item['input_budget']} | "
            f"{row['completed']}/{row['attempts']} | "
            f"{number(row['pass_at_1'], True)} | {number(row['pass_at_3'], True)} | "
            f"{row['hidden_tests_passed']} | {number(row['mean_untrimmed_input_tokens'])} | "
            f"{number(row['mean_first_request_input_tokens'])} | "
            f"{number(row['mean_first_request_compression'], True)} | "
            f"{row['provider_total_tokens']:,} |"
        )
    lines += [
        "",
        "压缩率是逐次本地首请求比例的平均，不是供应商总成本节省。停止原因与控制题单列：",
        "",
    ]
    for item in summaries:
        control = item["loss_control"]
        lines.append(
            f"- {item['path']}：主任务停止原因 "
            f"{json.dumps(item['primary']['stop_reasons'], ensure_ascii=False)}；"
            f"控制题 {control['completed']}/{control['attempts']}，"
            f"停止原因 {json.dumps(control['stop_reasons'], ensure_ascii=False)}。"
        )
    if "confirmation_passed" in report:
        lines += [
            "",
            f"确认是否达到预设门槛：{report['confirmation_passed']}。未达标时不重新选择预算或重跑失败样本。",
        ]
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm", type=Path)
    args = parser.parse_args()
    protocol = read(OUTPUT / "protocol.json")
    baseline = read(PROJECT / protocol["historical_baseline"])
    selection_path = OUTPUT / "budget-selection.json"
    if args.confirm:
        report = read(selection_path)
        assert report["selected_input_budget"] is not None
        assert report["protocol_sha256"] == digest(OUTPUT / "protocol.json")
        for item in [report["same_budget_regression"], *report["candidates"]]:
            assert digest(PROJECT / item["path"]) == item["sha256"]
        report["selection_record_sha256"] = digest(selection_path)
        assert report["dataset_manifest_sha256"] == digest(OUTPUT / "dataset-identity.json")
        report["confirmation"] = summarize(
            args.confirm.resolve(),
            baseline,
            budget=report["selected_input_budget"],
            repeats=protocol["confirmation"]["repeats"],
            include_control=True,
        )
        assert report["confirmation"]["source"] == report["same_budget_regression"]["source"]
        assert report["confirmation"]["settings"] == report["same_budget_regression"]["settings"]
        prior = [report["same_budget_regression"], *report["candidates"]]
        assert report["confirmation"]["batch_id"] not in {item["batch_id"] for item in prior}
        assert datetime.fromisoformat(
            report["confirmation"]["created_at"]
        ) > datetime.fromisoformat(report["created_at"])
        prior_traces = {
            Path(row["trace_path"]).resolve()
            for item in prior
            for row in item["all_attempts"]
            if row["trace_path"]
        }
        confirmation_traces = {
            Path(row["trace_path"]).resolve()
            for row in report["confirmation"]["all_attempts"]
            if row["trace_path"]
        }
        assert prior_traces.isdisjoint(confirmation_traces)
        report["confirmation_passed"] = qualifies(
            report["confirmation"],
            protocol["confirmation"]["minimum_primary_completed"],
            36,
            protocol["confirmation"]["minimum_mean_first_request_compression"],
        )
        report["confirmation_recorded_at"] = datetime.now(UTC).isoformat()
        destination = args.confirm.parent
    else:
        assert not selection_path.exists(), "Do not overwrite the frozen pre-confirmation selection"
        candidates = [
            summarize(
                ARTIFACTS / f"benchmark-v8-budget-{budget}" / "report.json",
                baseline,
                budget=budget,
                repeats=1,
                include_control=False,
            )
            for budget in protocol["selection"]["input_token_candidates"]
        ]
        for item in candidates:
            item["selection_qualified"] = qualifies(
                item,
                protocol["selection"]["minimum_completed"],
                12,
                protocol["selection"]["minimum_mean_first_request_compression"],
            )
        qualified = [item["input_budget"] for item in candidates if item["selection_qualified"]]
        report = {
            "created_at": datetime.now(UTC).isoformat(),
            "protocol_sha256": digest(OUTPUT / "protocol.json"),
            "dataset_manifest_sha256": digest(OUTPUT / "dataset-identity.json"),
            "protocol": protocol,
            "same_budget_regression": summarize(
                ARTIFACTS / "benchmark-v8-balanced" / "report.json",
                baseline,
                budget=6144,
                repeats=3,
                include_control=True,
            ),
            "candidates": candidates,
            "selected_input_budget": min(qualified) if qualified else None,
        }
        assert all(
            item["source"] == report["same_budget_regression"]["source"] for item in candidates
        )
        assert all(
            item["settings"] == report["same_budget_regression"]["settings"] for item in candidates
        )
        assert (
            len({item["batch_id"] for item in [report["same_budget_regression"], *candidates]})
            == len(candidates) + 1
        )
        destination = OUTPUT
    (destination / "budget-selection.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    )
    (destination / "budget-selection.md").write_text(markdown(report))
    print(
        json.dumps(
            {
                "selected_input_budget": report["selected_input_budget"],
                "confirmation_passed": report.get("confirmation_passed"),
                "output": str(destination),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
