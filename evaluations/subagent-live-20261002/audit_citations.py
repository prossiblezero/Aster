"""Post-hoc sensitivity check for real nested functions omitted by the frozen grader."""

import ast
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
PRIVATE = HERE.parents[1] / "artifacts/private/subagent-live-20261002"


def ranges(source):
    found = {}

    def visit(node, prefix=""):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            name = prefix + node.name
            found[name] = [node.lineno, node.end_lineno]
            prefix = name + "."
        for child in ast.iter_child_nodes(node):
            visit(child, prefix)

    visit(ast.parse(source))
    return found


def audit():
    rows = json.loads((HERE / "results.json").read_text())
    gold = {row["task"]: row["gold"] for row in json.loads((PRIVATE / "preflight.json").read_text())}
    records = []
    for row in rows:
        text = row["final"].strip()
        if text.startswith("```") and text.endswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]
        try:
            answers = json.loads(text)["answers"]
            by_id = {item["id"]: item for item in answers}
            ids_ok = len(by_id) == len(answers) and set(by_id) == {g["id"] for g in gold[row["task"]]}
        except (ValueError, KeyError, TypeError):
            by_id, ids_ok = {}, False
        workspace = PRIVATE / "workspaces" / (row["task"] + "--" + row["arm"])
        checks, omissions = [], []
        frozen_checks = {item["id"]: item for item in row["grade"].get("checks", [])}
        for expected in gold[row["task"]]:
            source_ranges = ranges((workspace / expected["evidence_path"]).read_text())
            # The supplementary index must preserve every range the frozen grader knew.
            assert all(source_ranges[key] == value for key, value in expected["evidence_ranges"].items())
            evidence = by_id.get(expected["id"], {}).get("evidence", {})
            if not isinstance(evidence, dict):
                evidence = {}
            span = source_ranges.get(evidence.get("symbol", ""))
            valid = bool(evidence.get("path") == expected["evidence_path"] and span
                         and type(evidence.get("line")) is int and span[0] <= evidence["line"] <= span[1])
            check = frozen_checks.get(expected["id"], {})
            if valid and not check.get("evidence"):
                assert evidence["symbol"] not in expected["evidence_ranges"]
                omissions.append(dict(id=expected["id"], evidence=evidence, actual_span=span))
            checks.append(bool(valid and check.get("observed") and check.get("regression")))
        success = (ids_ok and all(checks) and row["phase"] == "completed" and row["error"] is None
                   and row["workspace_unchanged"] and row["source_unchanged"])
        records.append(dict(task=row["task"], arm=row["arm"], frozen_passed=row["passed"],
                            supplementary_passed=success, supplementary_facts=sum(checks),
                            omitted_valid_symbols=omissions))
    summary = {arm: dict(
        frozen_passed=sum(r["frozen_passed"] for r in records if r["arm"] == arm),
        supplementary_passed=sum(r["supplementary_passed"] for r in records if r["arm"] == arm),
        supplementary_facts=sum(r["supplementary_facts"] for r in records if r["arm"] == arm),
        omitted_valid_citations=sum(len(r["omitted_valid_symbols"]) for r in records if r["arm"] == arm),
    ) for arm in dict.fromkeys(row["arm"] for row in rows)}
    report = dict(scope="Post-hoc nested-function index only; frozen scores and model answers unchanged",
                  summary=summary, cases=records)
    (HERE / "citation-audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    assert ranges("def outer():\n    def inner():\n        return 1\n    return inner()\n") == {
        "outer": [1, 4], "outer.inner": [2, 3],
    }
    audit()
