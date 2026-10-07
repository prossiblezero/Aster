"""Offline projection of all 12 recorded October 1 candidate-parallel handoffs."""

import hashlib
import json
from pathlib import Path

from aster.context.budget import BudgetCalculator
from aster.core.workers import WorkerManager

ROOT = Path(__file__).resolve().parents[2]
HISTORY = ROOT / "artifacts/private/subagent-evidence-20261001/workspaces"


def compare():
    counter = BudgetCalculator()
    assert counter.encoder is not None, "Install the project's existing tiktoken dependency"
    rows = []
    for workspace in sorted(HISTORY.glob("*--candidate_parallel")):
        count = 0
        for path in sorted((workspace / ".aster/runs").glob("*/checkpoint.json")):
            checkpoint = json.loads(path.read_text())
            if checkpoint["state"]["parent_run_id"]:
                continue
            for message in checkpoint["structured_history"]:
                old = message.get("content", "")
                if not isinstance(old, str) or not old.startswith("Worker handoff ("):
                    continue
                prefix, content = old.split("\n", 1)
                raw = json.loads(content)
                view = WorkerManager.model_view(raw)
                assert view["read_paths"] == list(raw["read_evidence"])
                assert {k: v for k, v in view.items() if k != "read_paths"} == {
                    k: v for k, v in raw.items() if k != "read_evidence"
                }
                new = prefix + "\n" + json.dumps(view, ensure_ascii=False)
                rows.append({
                    "task": workspace.name, "worker_id": raw["worker_id"],
                    "checkpoint": str(path.relative_to(ROOT)),
                    "message_sha256": hashlib.sha256(old.encode()).hexdigest(),
                    "read_paths": len(raw["read_evidence"]),
                    "before_bytes": len(old.encode()), "after_bytes": len(new.encode()),
                    "before_tokens": counter.count(old), "after_tokens": counter.count(new),
                })
                count += 1
        assert count == 2, (workspace.name, count)
    assert len(rows) == 12
    totals = {key: sum(row[key] for row in rows)
              for key in ("before_bytes", "after_bytes", "before_tokens", "after_tokens")}
    return {"scope": "12 fixed handoff message bodies; no model calls or task rescoring",
            "tokenizer": "cl100k_base (local estimate, not provider billing)",
            "totals": totals, "messages": rows}


if __name__ == "__main__":
    print(json.dumps(compare(), ensure_ascii=False, indent=2))
