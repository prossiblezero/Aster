"""Print the old20 cost comparison from frozen results; never write or call a model."""

import json
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
COUNTS = ("requests", "input_tokens", "cached_tokens", "output_tokens", "missing_usage")


def load(name):
    return json.loads((HERE / name).read_text())


def main():
    original_path = "general-coding-50-20261012/results.json"
    original = load(original_path)
    tasks = {r["task"] for r in original if r["agent"] == "aster" and r["exposure"] == "old20"}
    assert len(tasks) == 20
    original_rows = {}
    for agent in ("aster", "pi", "codex"):
        selected = [r for r in original if r["agent"] == agent and r["exposure"] == "old20"]
        assert len(selected) == 20 and {r["task"] for r in selected} == tasks
        assert all(r["graded"] for r in selected)
        original_rows[agent] = {key: sum(r[key] for r in selected) for key in COUNTS}
        original_rows[agent]["passed"] = sum(r["passed"] for r in selected)

    rows = [("优化前 Aster（c6b651d）", original_rows["aster"], original_path, "old20 / aster")]
    for version, folder in (("v9", "context-cache-20261013"), ("v10", "context-state-20261014")):
        source = folder + "/summary.json"
        summary = load(source)
        assert summary["planned"] == summary["finished"] == summary["graded"] == 20
        assert len(summary["tasks"]) == 20 and set(summary["tasks"]) == tasks
        key = "Aster " + version
        rows.append((key, summary["agents"][key], source, key))

    source = "turn-reduction-20261015/comparison-round1.json"
    comparison = load(source)
    first = comparison["per_attempt_spend"][0]
    assert comparison["round"] == 1 and comparison["tag"] == "v11-stage2"
    assert first["attempt"] == 1 and first["episodes"] == 20
    rows.append(("Aster v11 Stage 1+2", dict(first, passed=first["independent_passes"]),
                 source, "per_attempt_spend[0]"))
    rows += [(label, original_rows[agent], original_path, "old20 / " + agent)
             for label, agent in (("pi（历史）", "pi"), ("Codex（历史）", "codex"))]

    print("| 版本 | 首次独立通过 | 全部请求 | 输入 token | 缓存输入 | 未缓存输入 | 输出 token | 缓存占比 | 加权成本 | 缺用量请求 | 来源 |")
    print("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|")
    for label, row, source, selector in rows:
        assert all(type(row[key]) is int and row[key] >= 0 for key in COUNTS)
        assert 0 <= row["passed"] <= 20 and row["cached_tokens"] <= row["input_tokens"]
        assert row["input_tokens"] > 0 and row["missing_usage"] <= row["requests"]
        uncached = row["input_tokens"] - row["cached_tokens"]
        cost = (uncached * Decimal("1.25") + row["cached_tokens"] * Decimal("0.125")
                + row["output_tokens"] * Decimal("10")) / 1_000_000
        if "weighted_cost_observed" in row:
            assert cost == Decimal(str(row["weighted_cost_observed"]))
        cache_ratio = Decimal(row["cached_tokens"]) / row["input_tokens"]
        cells = [label, f'{row["passed"]}/20', f'{row["requests"]:,}',
                 f'{row["input_tokens"]:,}', f'{row["cached_tokens"]:,}', f'{uncached:,}',
                 f'{row["output_tokens"]:,}', f'{cache_ratio:.2%}', f'{cost:.6f}',
                 str(row["missing_usage"]), f"[{selector}]({source})"]
        print("| " + " | ".join(cells) + " |")


if __name__ == "__main__":
    main()
