"""Recompute this fixed comparison using the existing benchmark helpers; no API calls."""

from __future__ import annotations

import hashlib
import json
import sys
from operator import itemgetter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parents[1] / "scripts"))

from compare_benchmarks import compare_mode  # noqa: E402


def compare():
    reports = {
        name: json.loads((ROOT / f"{name}.json").read_text()) for name in ("baseline", "candidate")
    }
    environment = json.loads((ROOT / "environment.json").read_text())
    audit = json.loads((ROOT / "provider-audit.json").read_text())
    heads = {
        "baseline": "a3f338b43bfea61cc7bae563c07168f502cc34b5",
        "candidate": "edec1aa1a7ca84550bcf95df42c142489ee665fa",
    }
    for name, report in reports.items():
        assert report["source"]["git_head"] == heads[name]
        assert report["source"] == environment[name]["source"]
        assert report["settings"] == environment[name]["settings"]
        assert (report["mode"], report["selected_tasks"], report["repeats"]) == ("real", 5, 1)
        assert {row["task_id"] for row in report["rows"]} == {
            "multi-file-refactor",
            "debug-and-fix",
            "add-endpoint-with-test",
            "config-migration",
            "dependency-upgrade",
        }
        assert all(row["suite"] == "repository" for row in report["rows"])
        attempt_key = itemgetter("task_id", "variant", "attempt")
        assert len(audit[name]) == len(report["rows"])
        assert {attempt_key(row) for row in audit[name]} == {
            attempt_key(row) for row in report["rows"]
        }
    assert reports["baseline"]["settings"] == reports["candidate"]["settings"]
    groups = compare_mode(reports["baseline"], reports["candidate"])
    for group, paired in groups.items():
        suite, variant = group.split("/")
        for version, key in (("baseline", "before"), ("candidate", "after")):
            rows = [
                r
                for r in reports[version]["rows"]
                if (r["suite"], r["variant"]) == (suite, variant)
            ]
            requests = [r for r in audit[version] if r["variant"] == variant]
            paired[key].update(
                pass_rate=paired[key]["passed"] / len(rows),
                independent_code_passed=sum(
                    bool((r.get("verifier") or {}).get("passed")) for r in rows
                ),
                model_requests=sum(r["model_requests"] for r in rows)
                if all(r.get("model_requests") is not None for r in rows)
                else None,
                total_seconds=sum(r["seconds"] for r in rows),
                http_attempts=sum(r["http_attempts"] for r in requests),
                http_attempts_with_total_tokens=sum(
                    r["http_attempts_with_total_tokens"] for r in requests
                ),
                http_attempts_without_usage=sum(r["http_attempts_without_usage"] for r in requests),
                monetary_cost=None,
            )
    return {
        "scope": "5 existing repository tasks; one attempt per tool profile and version",
        "monetary_cost_note": "Unmeasured: gateway prices and invoice unavailable.",
        "usage_coverage_note": (
            "Inherited usage_summary coverage counts logical model requests. HTTP retry coverage "
            "is reported separately. Token ratios compare reported usage only; missing retry "
            "usage is unknown, not zero."
        ),
        "input_sha256": {
            name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in (
                "baseline.json",
                "candidate.json",
                "environment.json",
                "provider-audit.json",
                "PROTOCOL.md",
            )
        },
        "groups": groups,
    }


if __name__ == "__main__":
    (ROOT / "comparison.json").write_text(
        json.dumps(compare(), ensure_ascii=False, indent=2) + "\n"
    )
