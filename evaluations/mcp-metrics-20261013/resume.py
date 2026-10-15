"""Resume unstarted episodes after an independently recorded provider health check."""
from __future__ import annotations

import asyncio

import analyze
import evaluate as experiment


async def main():
    experiment.check_freeze()
    health = experiment.read(experiment.HERE / "healthcheck.json")
    assert health["returned_reply"] and health["usage"].get("total_tokens")
    cases = experiment.read(experiment.HERE / "cases.json")
    results = [experiment.read(p) for p in (experiment.HERE / "results").glob("*.json")]
    completed = {r["id"] for r in results}
    started = {p.name for p in (experiment.PRIVATE / "runs").iterdir()}
    assert started == completed, "Unfinished workspace requires a separate audit"
    analyze.audit(results, cases)

    async def pair(index, case, repeat):
        arms = ["eager", "lazy"] if (index + repeat) % 2 else ["lazy", "eager"]
        for arm in arms:
            if f"{case['id']}--{arm}--{repeat}" in completed:
                continue
            row = await experiment.run_one(case, arm, repeat)
            if not row["model_requests"] or row["requests_without_usage"] == row["model_requests"]:
                raise RuntimeError("Model unavailable again; preserve results and stop")

    for offset in range(0, len(cases), 2):
        for repeat in (1, 2):
            # Finish both active pairs before stopping, preserving each run's ledger.
            outcomes = await asyncio.gather(
                *(pair(i, cases[i], repeat) for i in range(offset, min(offset + 2, len(cases)))),
                return_exceptions=True,
            )
            for outcome in outcomes:
                if isinstance(outcome, BaseException):
                    raise outcome


if __name__ == "__main__":
    asyncio.run(main())
