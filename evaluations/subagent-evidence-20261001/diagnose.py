"""Post-hoc explanation of frozen grades; never repair answers or change scores."""
import json
from pathlib import Path
from statistics import median

HERE = Path(__file__).resolve().parent
results = json.loads((HERE / "results.json").read_text())
tasks = json.loads((HERE.parents[1] / "datasets/subagent-light-20260930/instances.json").read_text())
expected_ids = {t["id"]: {q["id"] for g in t["groups"] for q in g["questions"]} for t in tasks}
arms = ("previous_parallel", "candidate_solo", "candidate_parallel")
assert len(results) == 18 and len({(r["task"], r["arm"]) for r in results}) == 18
diagnostics = []
for r in results:
    text = r["final"].strip()
    if text.startswith("```") and text.endswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0]
    try:
        answers = json.loads(text)["answers"]
        ids = [a["id"] for a in answers]
        ids_ok = len(ids) == len(set(ids)) and set(ids) == expected_ids[r["task"]]
    except (ValueError, KeyError, TypeError):
        ids_ok = False
    checks = r["grade"].get("checks", [])
    behavior = ids_ok and bool(checks) and all(c["observed"] and c["regression"] for c in checks)
    diagnostics.append(dict(
        task=r["task"], arm=r["arm"], parse_error=r["grade"]["parse_error"],
        behavior_pass=behavior and r["phase"] == "completed" and r["error"] is None
        and r["workspace_unchanged"] and r["source_unchanged"],
        behavior_facts=sum(c["observed"] and c["regression"] for c in checks),
        citation_only=sum(c["observed"] and c["regression"] and not c["evidence"] for c in checks),
        value_errors=[c["id"] for c in checks if not c["observed"]],
        classification_errors=[c["id"] for c in checks if not c["regression"]],
    ))
summary = {}
for arm in arms:
    rows = [r for r in results if r["arm"] == arm]
    ds = [r for r in diagnostics if r["arm"] == arm]
    summary[arm] = dict(
        behavior_pass=sum(d["behavior_pass"] for d in ds),
        behavior_facts=sum(d["behavior_facts"] for d in ds),
        citation_only=sum(d["citation_only"] for d in ds),
        value_errors=sum(len(d["value_errors"]) for d in ds),
        classification_errors=sum(len(d["classification_errors"]) for d in ds),
        parse_errors=sum(d["parse_error"] for d in ds),
        model_calls=sum(r["model_calls"] for r in rows),
        tool_calls=sum(r["tool_calls"] for r in rows),
        children_started=sum(r["workers_started"] for r in rows),
        children_completed=sum(r["workers_completed"] for r in rows),
        median_seconds=median(r["seconds"] for r in rows),
    )
pairs = {}
by_key = {(r["task"], r["arm"]): r for r in results}
for baseline in arms[:2]:
    both = [(by_key[(t["id"], baseline)], by_key[(t["id"], "candidate_parallel")]) for t in tasks]
    pairs[baseline] = dict(
        wins=sum(b["passed"] and not a["passed"] for a, b in both),
        losses=sum(a["passed"] and not b["passed"] for a, b in both),
        ties=sum(a["passed"] == b["passed"] for a, b in both),
        median_seconds_ratio=median(b["seconds"] / a["seconds"] for a, b in both),
        median_tokens_ratio=median(b["usage"]["total_tokens"] / a["usage"]["total_tokens"] for a, b in both),
    )
assert all(r["source_unchanged"] and r["workspace_unchanged"] and r["usage_matches"] for r in results)
report = dict(summary=summary, pairs=pairs, cases=diagnostics)
(HERE / "diagnostics.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
print(json.dumps(dict(summary=summary, pairs=pairs), ensure_ascii=False, indent=2))
