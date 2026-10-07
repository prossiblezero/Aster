"""Post-hoc check of whether parent errors were inherited from a child answer."""

import json
from pathlib import Path

from evaluate import OUT, base, save

HERE = Path(__file__).resolve().parent


def answers(text):
    text = text.strip()
    if text.startswith("```") and text.endswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0]
    try:
        values = json.loads(text)["answers"]
        return {value["id"]: value for value in values}
    except (ValueError, KeyError, TypeError):
        return {}


def audit():
    driver = base(OUT / "candidate/src")
    tasks = {task["id"]: task for task in driver.bank()}
    preflight = {row["task"]: row["gold"] for row in json.loads((OUT / "preflight.json").read_text())}
    records = []
    for result_path in sorted((OUT / "runs").glob("*/result.json")):
        result = json.loads(result_path.read_text())
        task = tasks[result["task"]]
        parent = answers(result["final"])
        parent_checks = {item["id"]: item for item in result["grade"].get("checks", [])}
        workspace = OUT / "workspaces" / result_path.parent.name
        for checkpoint in sorted((workspace / ".aster/runs").glob("*/checkpoint.json")):
            state = json.loads(checkpoint.read_text())["state"]
            if not state["parent_run_id"]:
                continue
            group = next(group for group in task["groups"]
                         if "Complete only work package " + group["name"] + "." in state["task"]["prompt"])
            ids = {item["id"] for item in group["questions"]}
            expected = [item for item in preflight[task["id"]] if item["id"] in ids]
            child_grade = driver.grade(state["final"], expected, task["kind"] == "review")
            child = answers(state["final"])
            child_checks = {item["id"]: item for item in child_grade.get("checks", [])}
            comparisons = []
            for key in sorted(ids & child.keys() & parent.keys()):
                before, after = child_checks[key], parent_checks[key]
                comparisons.append(dict(
                    id=key,
                    child_behavior_correct=before["observed"] and before["regression"],
                    parent_behavior_correct=after["observed"] and after["regression"],
                    child_citation_valid=before["evidence"], parent_citation_valid=after["evidence"],
                    same_citation=child[key].get("evidence") == parent[key].get("evidence"),
                    same_value=json.dumps(child[key].get("observed"), sort_keys=True)
                    == json.dumps(parent[key].get("observed"), sort_keys=True),
                ))
            records.append(dict(task=task["id"], arm=result["arm"], worker_run_id=state["run_id"],
                                package=group["name"], phase=state["phase"], grade=child_grade,
                                comparisons=comparisons))
    save(HERE / "child-diagnostics.json", records)
    print(json.dumps(dict(
        children=len(records), strict_pass=sum(row["grade"]["passed"] for row in records),
        parent_corrected=sum(not item["child_behavior_correct"] and item["parent_behavior_correct"]
                             for row in records for item in row["comparisons"]),
        parent_introduced=sum(item["child_behavior_correct"] and not item["parent_behavior_correct"]
                              for row in records for item in row["comparisons"]),
        inherited_errors=sum(not item["child_behavior_correct"] and not item["parent_behavior_correct"]
                             and item["same_value"] for row in records for item in row["comparisons"]),
        parent_introduced_citation_errors=sum(item["child_citation_valid"] and not item["parent_citation_valid"]
                                             for row in records for item in row["comparisons"]),
    )))


if __name__ == "__main__":
    audit()
