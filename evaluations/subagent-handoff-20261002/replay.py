"""Offline replay of frozen handoffs and rejected selections; never calls a model."""
from __future__ import annotations

import asyncio
import hashlib
import json
from collections import Counter
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

from aster.context.budget import BudgetCalculator
from aster.core.workers import WorkerManager
from aster.safety.redaction import Redactor
from aster.storage.workspace import Workspace
from aster.tools.schemas import WorkerFinish
from aster.tools.workflow import WorkflowTools

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
HISTORY = REPO / "artifacts/private/subagent-wait-modes-20261002"


async def replay():
    counter = BudgetCalculator()
    assert counter.encoder is not None
    totals, reasons, shortened, inputs, projections, rejections = Counter(), Counter(), Counter(), {}, [], []

    def read(path):
        data = path.read_bytes()
        inputs[str(path.relative_to(REPO))] = hashlib.sha256(data).hexdigest()
        return data.decode()

    episodes = json.loads(read(REPO / "evaluations/subagent-wait-modes-20261002/results.json"))
    for episode in episodes:
        name = f"{episode['task']}--{episode['arm']}--{episode['repeat']}"
        root = HISTORY / "workspaces" / name
        run_dir = root / ".aster/runs"
        events = {path.parent.name: [json.loads(line) for line in read(path).splitlines()]
                  for path in sorted(run_dir.glob("*/trace.jsonl"))}
        for path in sorted(run_dir.glob("*/handoff.json")):
            raw = json.loads(read(path))
            # Exact previous model_view projection, before the display-only change.
            before = {key: value for key, value in raw.items() if key != "read_evidence"}
            before["read_paths"] = list(raw["read_evidence"])
            original = deepcopy(raw)
            after = WorkerManager.model_view(raw)
            assert raw == original
            assert after["answer"] == before["answer"]
            assert len(after["source_excerpts"]) == len(raw["source_excerpts"])
            for source, view in zip(raw["source_excerpts"], after["source_excerpts"], strict=True):
                assert {k: v for k, v in source.items() if k not in {"content", "evidence_id", "source_sha256"}} == {
                    k: v for k, v in view.items() if k != "content"}
                assert view["content"].splitlines() == [
                    f"{line}: {text}" for line, text in enumerate(source["content"].splitlines(), source["start"])]
            counts = {"before_tokens": counter.count(before), "after_tokens": counter.count(after)}
            totals.update(counts)
            totals.update(handoffs=1, excerpts=len(raw["source_excerpts"]))
            projections.append({"episode": name, "run_id": path.parent.name, **counts})
        for path in sorted(run_dir.glob("*/checkpoint.json")):
            state = json.loads(read(path))["state"]
            if not state.get("parent_run_id"):
                continue
            for row in events[path.parent.name]:
                if (row["event"] != "tool_result" or row["call"]["name"] != "worker_finish"
                        or row["outcome"]["reason"] not in {"handoff_source_unread", "handoff_source_too_large"}):
                    continue
                # Use only snapshot-authorized, successful observations predating this attempt.
                available = {key: [event for event in rows if event["time"] < row["time"]]
                             for key, rows in events.items()}
                authorized = {event["outcome"]["evidence_id"]: key
                              for key, rows in available.items() for event in rows
                              if event["event"] == "tool_result" and event["call"]["name"] == "read_file"
                              and event["outcome"]["status"] == "success"
                              and state["worker_reads"].get(event["outcome"]["evidence_id"]) == key}
                for rows in available.values():
                    for event in rows:
                        if (event["event"] == "tool_result" and event["call"]["name"] == "read_file"
                                and event["outcome"]["evidence_id"] in authorized):
                            read(root / event["outcome"]["data"]["path"])
                services = SimpleNamespace(
                    state=SimpleNamespace(parent_run_id=state["parent_run_id"], worker_reads=authorized),
                    workspace=Workspace(root),
                    evidence=SimpleNamespace(events=available.__getitem__, redactor=Redactor()),
                )
                args = WorkerFinish.model_validate(row["call"]["arguments"]).model_dump()
                result = await WorkflowTools.execute(services, "worker_finish", args)
                assert result.status == "blocked", "Original invalid requests must remain rejected"
                reasons[result.reason] += 1
                record = {"episode": name, "run_id": path.parent.name, "seq": row["seq"],
                          "old_reason": row["outcome"]["reason"], "new_reason": result.reason,
                          "feedback": result.content}
                if result.reason == "handoff_source_unknown":
                    changed = False
                    for source in args["sources"]:
                        reference = source["evidence_id"]
                        if reference not in authorized and len(reference) > 12:
                            matching = [key for key in authorized if key.startswith(reference[:12])]
                            if len(matching) == 1:
                                source["evidence_id"] = reference[:12]
                                changed = True
                    if changed:
                        retry = await WorkflowTools.execute(services, "worker_finish", args)
                        key = retry.reason or retry.status
                        shortened[key] += 1
                        record["explicit_short_reference_projection"] = key
                rejections.append(record)
    assert len(projections) == 48 and len(rejections) == 27
    assert reasons == {"handoff_source_unknown": 17, "handoff_source_too_large": 10}
    assert all(hashlib.sha256((REPO / path).read_bytes()).hexdigest() == digest for path, digest in inputs.items())
    return {"scope": "48 saved handoffs counted once; 27 rejected calls replayed without model decisions or task rescoring",
            "tokenizer": "cl100k_base, local message-body estimate, not provider usage",
            "totals": totals, "rejection_reasons": reasons, "explicit_short_reference_projection": shortened,
            "projections": projections, "rejections": rejections, "input_sha256": inputs}


if __name__ == "__main__":
    result = asyncio.run(replay())
    (HERE / "offline.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({key: value for key, value in result.items()
                      if key not in {"projections", "rejections", "input_sha256"}}, ensure_ascii=False))
