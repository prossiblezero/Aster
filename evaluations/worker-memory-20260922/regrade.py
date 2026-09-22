"""Regrade an underspecified input representation, preserving original model attempts."""

import argparse
import tempfile
from pathlib import Path

import cases
import run as driver

CHECK = """
import export_rows
import inspect
assert 'normalize_id' in inspect.getsource(export_rows)
errors = []
for convert in [lambda ident, value: {'id': ident, 'value': value}, lambda ident, value: (ident, value)]:
    try:
        assert export_rows.render_csv([convert(' A ', 'x,y')]) == 'id,value\\na,"x,y"\\n'
        assert export_rows.render_csv([]) == 'id,value\\n'
        try:
            export_rows.render_csv([convert('X', 1), convert(' x ', 2)])
        except ValueError:
            pass
        else:
            raise AssertionError('duplicate normalized id was accepted')
    except Exception as exc:
        errors.append(type(exc).__name__ + ': ' + str(exc))
    else:
        break
else:
    raise AssertionError(errors)
"""


async def main(output):
    case = next(c for c in cases.worker_cases() if c["id"] == "dependent-export")
    modules = [dict(m) for m in case["modules"]]
    modules[1]["checks"] = CHECK
    driver.support.DATASETS = output / "graders"
    name = "dependent-export-revised.py"
    (driver.support.DATASETS / name).write_text(cases.grader(modules))
    with tempfile.TemporaryDirectory(prefix="aster-row-regrade-") as directory:
        root = Path(directory)
        driver.put_files(root, {m["path"]: m["reference"] for m in modules})
        reference = await driver.support.external_check(root, name)
        assert reference["passed"]
        (root / "export_rows.py").write_text('def render_csv(rows):\n    return "id,value\\n"\n')
        negative = await driver.support.external_check(root, name)
        assert not negative["passed"]
    results = []
    for arm in ["solo", "workers"]:
        root = output / "workspaces" / f"worker-dependent-export-{arm}"
        before = driver.product_hashes(root)
        result = await driver.support.external_check(root, name)
        assert before == driver.product_hashes(root)
        results.append({"name": f"worker-dependent-export-{arm}", "grade": result})
    driver.save(
        output / "regrade.json",
        {
            "reason": "underspecified row representation",
            "reference": reference,
            "negative": negative,
            "results": results,
        },
    )
    print([(r["name"], r["grade"]["passed"]) for r in results])


if __name__ == "__main__":
    import asyncio

    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(main(parser.parse_args().output.resolve()))
