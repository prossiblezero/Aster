"""Aggregate immutable run summaries, without double-counting attempt/reply usage."""

import json
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCENARIOS = {
    "lab": ["lab-initial", "lab-recovery", "lab-readme-repair"],
    "study": ["study-initial", "study-recovery", "study-product-repair"],
    "maintenance": ["maintenance-initial", "maintenance-switch-supplement"],
}


def main():
    results, seen, seen_events = {}, set(), set()
    for scenario, names in SCENARIOS.items():
        usage, classes, labels, flags = Counter(), Counter(), Counter(), Counter()
        rows, seconds, steps, turns, unknown, attempts = [], 0.0, 0, 0, 0, 0
        for name in names:
            directory = HERE / "runs" / name
            path = directory / "summary.json"
            if not path.exists():
                raise FileNotFoundError(path)
            summary = json.loads(path.read_text())
            seconds += summary["elapsed_seconds"]
            row = {"name": name, "seconds": summary["elapsed_seconds"], "run_ids": []}
            for run in summary.get("runs", []):
                run_id = run["run_id"]
                if run_id in seen:
                    continue
                seen.add(run_id)
                row["run_ids"].append(run_id)
                steps += run["step"]
                turns += run["turn"]
                usage.update(run["usage"])
            rows.append(row)
            for events_path in directory.glob("*events.json"):
                for event in json.loads(events_path.read_text()):
                    key = (event.get("run_id"), event.get("seq"))
                    if event.get("event") != "provider_attempt" or key in seen_events:
                        continue
                    seen_events.add(key)
                    attempts += 1
                    classes[event["classification"]] += 1
                    unknown += not event.get("usage_observed", False)
                    labels[(event["requested_model"], event.get("response_model", ""))] += 1
                    flags.update(event.get("flags", []))
        results[scenario] = {
            "runs": rows,
            "steps": steps,
            "turns": turns,
            "elapsed_seconds_sum": round(seconds, 3),
            "usage": dict(usage),
            "provider_attempts": attempts,
            "attempts_without_usage": unknown,
            "classifications": dict(classes),
            "labels": [{"requested": a, "responded": b, "count": n}
                       for (a, b), n in sorted(labels.items())],
            "diagnostic_flags": dict(flags),
            "cache_read_fraction_of_reported_input": (
                usage["cache_read_input_tokens"] / usage["input_tokens"]
                if usage["input_tokens"] else None
            ),
        }
    output = {
        "method": "Sum each distinct RunState.usage once; attempts describe coverage only. "
                  "Missing usage is unknown, not zero. Elapsed sums are model task executions, "
                  "excluding operator engineering and independent grading; not total wall time.",
        "scenarios": results,
        "known_total_tokens": sum(row["usage"].get("total_tokens", 0)
                                  for row in results.values()),
        "attempts_without_usage": sum(row["attempts_without_usage"] for row in results.values()),
    }
    (HERE / "costs.json").write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
