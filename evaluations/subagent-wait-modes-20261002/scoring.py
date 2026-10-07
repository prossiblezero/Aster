"""Correct the nested-symbol index before freezing this evaluation's gold data."""

import ast
import json


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


def preflight(b):
    import tempfile
    from pathlib import Path

    # A nested function in a method and under control flow must retain its scope.
    assert ranges("class C:\n def f(self):\n  if True:\n   async def g():\n    return 1\n") == {
        "C": [1, 5], "C.f": [2, 5], "C.f.g": [4, 5],
    }
    records = []
    for task in b.bank():
        with tempfile.TemporaryDirectory(prefix="aster-wait-oracle-") as folder:
            root = Path(folder) / "workspace"
            b.prepare(task, root)
            gold = b.oracle(task, root)
            additions = {}
            for row in gold:
                index = ranges((root / row["evidence_path"]).read_text())
                assert all(index[key] == span for key, span in row["evidence_ranges"].items())
                additions[row["id"]] = sorted(index.keys() - row["evidence_ranges"].keys())
                row["evidence_ranges"] = index
            answers = [dict(id=x["id"], observed=x["observed"],
                            evidence=dict(path=x["evidence_path"], symbol=x["evidence_symbol"],
                                          line=x["lines"][0]),
                            **({"regression": x["regression"]} if task["kind"] == "review" else {}))
                       for x in gold]

            def passes(rows, gold=gold, review=task["kind"] == "review"):
                return b.grade(json.dumps(dict(answers=rows)), gold, review)["passed"]

            assert passes(answers)
            negative_count = 0
            for value in (answers[:-1], answers + [answers[0]]):
                assert not passes(value)
                negative_count += 1
            for field, value in (("observed", "__wrong__"), ("line", 0),
                                 ("line", gold[0]["lines"][1] + 1),
                                 ("symbol", "__missing_symbol__"),
                                 ("symbol", gold[0]["evidence_symbol"] + " / other")):
                wrong = json.loads(json.dumps(answers))
                target = wrong[0] if field == "observed" else wrong[0]["evidence"]
                target[field] = value
                assert not passes(wrong)
                negative_count += 1
            nested_controls = 0
            for i, row in enumerate(gold):
                for name in additions[row["id"]]:
                    nested = json.loads(json.dumps(answers))
                    nested[i]["evidence"].update(symbol=name, line=row["evidence_ranges"][name][0])
                    assert passes(nested)
                    nested_controls += 1
            records.append(dict(task=task["id"], gold=gold, added_symbols=additions,
                                negative_controls=negative_count, nested_positive_controls=nested_controls,
                                public_hashes=b.ledger_driver.product_hashes(root)))
    assert any(r["nested_positive_controls"] for r in records)
    return records
