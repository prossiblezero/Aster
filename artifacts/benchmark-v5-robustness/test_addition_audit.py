"""Offline AST audit of requested test additions; does not execute candidate code."""

from __future__ import annotations

import ast
import json
from pathlib import Path


class StripDocstrings(ast.NodeTransformer):
    def generic_visit(self, node):
        node = super().generic_visit(node)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
            if node.body and isinstance(node.body[0], ast.Expr):
                value = node.body[0].value
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    node.body = node.body[1:]
        return node


def disabled(nodes):
    return any(
        isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "__test__" for target in node.targets)
        and isinstance(node.value, ast.Constant)
        and node.value.value is False
        for node in nodes
    )


def collect_tests(root):
    tests, errors = {}, []
    if not Path(root).is_dir():
        return tests, [{"file": str(root), "error": "missing_directory"}]
    for path in sorted(Path(root).rglob("*.py")):
        relative = path.relative_to(root)
        if any(part.startswith(".") or part == "__pycache__" for part in relative.parts):
            continue
        if not (path.name.startswith("test_") or path.name.endswith("_test.py")):
            continue
        try:
            module = StripDocstrings().visit(ast.parse(path.read_text()))
        except (OSError, UnicodeError, SyntaxError) as exc:
            errors.append({"file": str(relative), "error": type(exc).__name__})
            continue
        if disabled(module.body):
            continue
        for node in module.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                members, prefix = [node], ""
            elif (
                isinstance(node, ast.ClassDef)
                and node.name.startswith("Test")
                and not disabled(node.body)
                and not any(
                    isinstance(item, ast.FunctionDef) and item.name == "__init__"
                    for item in node.body
                )
            ):
                members, prefix = node.body, node.name + "."
            else:
                continue
            for function in members:
                if isinstance(
                    function, (ast.FunctionDef, ast.AsyncFunctionDef)
                ) and function.name.startswith("test_"):
                    tests[f"{relative}::{prefix}{function.name}"] = function
    return tests, errors


def deletion_only(before, after):
    """True if after is unchanged or obtained solely by deleting AST list entries."""
    if isinstance(before, ast.AST) and isinstance(after, ast.AST):
        return type(before) is type(after) and all(
            deletion_only(getattr(before, field), getattr(after, field)) for field in before._fields
        )
    if isinstance(before, list) and isinstance(after, list):
        cursor = iter(before)
        return all(any(deletion_only(old, item) for old in cursor) for item in after)
    return before == after


def canonical(function):
    # Renaming/moving an otherwise identical test does not add a test requirement.
    name = function.name
    function.name = "__test__"
    rendered = ast.dump(function, include_attributes=False)
    function.name = name
    return rendered


def audit_added_tests(fixture, candidate):
    before, fixture_errors = collect_tests(Path(fixture))
    after, candidate_errors = collect_tests(Path(candidate))
    original_bodies = {canonical(node) for node in before.values()}
    added, modified, deletion_changes = [], [], []
    for key, node in after.items():
        if key not in before:
            if canonical(node) not in original_bodies:
                added.append(key)
        elif ast.dump(before[key]) != ast.dump(node):
            if deletion_only(before[key], node):
                deletion_changes.append(key)
            else:
                modified.append(key)
    return {
        "test_addition_structure_passed": bool(added or modified)
        and not candidate_errors
        and not fixture_errors,
        "added": added,
        "modified": modified,
        "deleted": sorted(before.keys() - after.keys()),
        "deletion_only_changes": deletion_changes,
        "fixture_test_count": len(before),
        "candidate_test_count": len(after),
        "parse_errors": {"fixture": fixture_errors, "candidate": candidate_errors},
        "interpretation": "Static evidence of added/modified tests following default pytest names; not proof of actual execution or assertion quality.",
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path.cwd())
    parser.add_argument("--runs", type=Path, required=True)
    args = parser.parse_args()
    datasets = args.project / "datasets"
    tasks = {
        case["id"]: case
        for case in json.loads((datasets / "context_task_cases.json").read_text())["cases"]
    }
    rows = []
    for result_path in sorted(args.runs.rglob("result.json")):
        result = json.loads(result_path.read_text())
        case = tasks.get(result.get("task_id"))
        candidate = result_path.parent / "candidate"
        if case is None:
            continue
        required = "add focused tests" in case["prompt"]
        audit = audit_added_tests(datasets / case["fixture"], candidate)
        attempt = result.get("attempt")
        if attempt is None:
            variant, separator, suffix = result_path.parent.name.rpartition("-")
            if not separator or variant != result["variant"] or not suffix.isdigit():
                raise ValueError(f"Cannot recover attempt number from {result_path.parent}")
            attempt = int(suffix)
        if not isinstance(attempt, int) or attempt < 1:
            raise ValueError(f"Invalid attempt number for {result_path}: {attempt}")
        rows.append(
            {
                "task_id": case["id"],
                "variant": result["variant"],
                "attempt": attempt,
                "result_path": str(result_path),
                "original_passed": result["passed"],
                "hidden_tests_passed": result.get("verifier", {}).get("passed", False),
                "test_addition_required": required,
                **audit,
                "completed_with_test_structure": result["passed"]
                and (not required or audit["test_addition_structure_passed"]),
            }
        )
    report = {
        "mode": "offline_test_addition_structure_audit",
        "scope": "All completed context result records found under --runs; rerun after the experiment ends.",
        "limitations": [
            "AST structure does not prove test execution, assertion quality, coverage, or complete instruction following.",
            "Recognizes standard test-file names, test_* functions, and Test* class methods; custom collectors, dynamic tests, and nonstandard unittest classes may require manual review.",
            "Changes only to external fixtures, helpers, or parametrization data outside the test function can be missed.",
            "Comments, docstrings, deleted tests/statements, and unchanged renamed or moved tests do not count as additions.",
            "original_passed retains runtime completion plus hidden tests; hidden_tests_passed records only hidden functional tests; completed_with_test_structure adds the separate structural requirement to original_passed.",
        ],
        "rows": rows,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
