"""Apply the preregistered fixed-success gate without additional model calls."""
import json

import evaluate as runner

ANCHORS = ("django__django-17029", "terminal-schemelike-metacircular-eval")
METRICS = ("input_tokens", "cached_tokens", "output_tokens", "total_tokens", "requests", "missing_usage", "seconds")


def totals(rows):
    result = {key: sum(r[key] for r in rows) for key in METRICS}
    result["uncached_input"] = result["input_tokens"] - result["cached_tokens"]
    return result


def costs(before, after):
    return [{"r": r, "q": q,
             "baseline": before["uncached_input"] + r * before["cached_tokens"] + q * before["output_tokens"],
             "candidate": after["uncached_input"] + r * after["cached_tokens"] + q * after["output_tokens"]}
            for r in (0.1, 0.25) for q in (1, 4, 8)]


def main():
    phases = ("pilot-baseline", "pilot-candidate")
    summaries = [runner.load(runner.ROOT / p / "summary.json") for p in phases]
    rows = [runner.load(runner.ROOT / p / "results.json") for p in phases]
    indexed = [{r["task"]: r for r in group} for group in rows]
    fixed = [[group[task] for task in ANCHORS] for group in indexed]
    all_totals, fixed_totals = [totals(r) for r in rows], [totals(r) for r in fixed]
    before, after = fixed_totals
    weighted = costs(before, after)
    passed = [{r["task"] for r in group if r["passed"]} for group in rows]
    checks = {
        "all_graded_without_errors": all(s["finished"] == s["graded"] == 4 and s["grading_errors"] == 0 for s in summaries),
        "no_lost_success": passed[0] <= passed[1],
        "fixed_successes_delivered": all(r["passed"] and r["exit_code"] == 0 and bool(r["final"]) for group in fixed for r in group),
        "fixed_success_usage_complete": all(r["missing_usage"] == 0 for group in fixed for r in group),
        "fixed_total_tokens_down_5_percent": after["total_tokens"] <= before["total_tokens"] * 0.95,
        "fixed_requests_not_increased": after["requests"] <= before["requests"],
        "fixed_uncached_down_5_percent": after["uncached_input"] <= before["uncached_input"] * 0.95,
        "fixed_weighted_cost_down_5_percent": all(r["candidate"] <= r["baseline"] * 0.95 for r in weighted),
        "fixed_seconds_down_10_percent": after["seconds"] <= before["seconds"] * 0.9,
        "no_fixed_task_over_10_percent_slower": all(indexed[1][t]["seconds"] <= indexed[0][t]["seconds"] * 1.1 for t in ANCHORS),
        "all_cost_weights_not_increased": all(r["candidate"] <= r["baseline"] for r in costs(*all_totals)),
        "missing_usage_not_increased": all_totals[1]["missing_usage"] <= all_totals[0]["missing_usage"],
    }
    result = {"proceed_to_full": all(checks.values()), "checks": checks,
              "fixed_tasks": ANCHORS, "fixed_totals": dict(zip(phases, fixed_totals)),
              "all_totals": dict(zip(phases, all_totals)), "fixed_cost_weights": weighted,
              "all_cost_weights": costs(*all_totals),
              "gained": sorted(passed[1] - passed[0]), "lost": sorted(passed[0] - passed[1])}
    runner.save(runner.ROOT / "gate.json", result)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
