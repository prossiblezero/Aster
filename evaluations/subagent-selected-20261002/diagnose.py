"""Audit provider failures separately from answer quality; do not repair scores."""

import json
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
PRIVATE = HERE.parents[1] / "artifacts/private/subagent-selected-20261002"
rows = json.loads((HERE / "results.json").read_text())
assert len(rows) == 24 and len({(row["task"], row["arm"]) for row in rows}) == 24
errors, attempts, models = Counter(), [], Counter()
for row in rows:
    workspace = PRIVATE / "workspaces" / (row["task"] + "--" + row["arm"])
    for trace in (workspace / ".aster/runs").glob("*/trace.jsonl"):
        for line in trace.read_text().splitlines():
            event = json.loads(line)
            if event["event"] == "provider_error":
                error = json.loads(event["error"])
                errors[str(error["status"])] += 1
    ledger = json.loads((PRIVATE / "runs" / workspace.name / "request-ledger.json").read_text())
    requests = ledger["requests"] if isinstance(ledger, dict) else ledger
    attempts.extend(attempt for request in requests for attempt in request["attempts"])
    models.update(row["response_models"])
summary = dict(
    episodes=len(rows), completed_parents=sum(row["phase"] == "completed" for row in rows),
    answer_samples=sum(bool(row["final"].strip()) for row in rows),
    provider_statuses=dict(errors), http_attempts=len(attempts),
    attempt_classifications=dict(Counter(a.get("classification", "") for a in attempts)),
    http_without_total_usage=sum("total_tokens" not in a.get("usage", {}) for a in attempts),
    reported_tokens=sum(row["usage"].get("total_tokens", 0) for row in rows),
    usage_known=all("total_tokens" in a.get("usage", {}) for a in attempts),
    response_models=dict(models),
    workspaces_unchanged=all(row["workspace_unchanged"] for row in rows),
    sources_unchanged=all(row["source_unchanged"] for row in rows),
    runner_errors=[row["error"] for row in rows if row["error"]],
    interpretation="No answer-quality, latency or token-efficiency comparison is valid without successful model responses.",
)
(HERE / "diagnostics.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
print(json.dumps(summary, ensure_ascii=False, indent=2))
