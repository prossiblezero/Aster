"""Versioned lab acceptance: frozen original checks plus literal-text regressions."""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import os
import posixpath
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

ORIGINAL_PATH = Path(__file__).resolve().parent.parent / "v0.2-release/lab-grader.py"
ORIGINAL_SHA256 = "ca09228d1ab81db69e23d2adb5367490edb1fca18d5b1752880a618200fc928f"
if hashlib.sha256(ORIGINAL_PATH.read_bytes()).hexdigest() != ORIGINAL_SHA256:
    raise RuntimeError("Frozen original lab grader changed; do not silently change the oracle")
SPEC = importlib.util.spec_from_file_location("frozen_lab_grader", ORIGINAL_PATH)
ORIGINAL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ORIGINAL)

# These are additional inputs under the unchanged public CSV/workbook contract.
FORMULA_CASES = [
    (
        "selected_formula_values",
        ["measurement", "comment"],
        [
            ["=1+1", "normal"],
            ["=SUM(A1:A2)", "=unselected_text"],
            ["  =1+1  ", " whitespace "],
            ["@SUM(A1:A2)", "001"],
            ["001", "=1+1"],
            ["-2", ""],
            ["+3", "中文"],
            ["", "missing"],
        ],
        ["measurement"],
    ),
    (
        "formula_dynamic_column_names",
        ["run_id", "=1+1", "temp", "=note"],
        [
            ["001", "2", "=3+3", "=text"],
            ["002", "=1+1", "4", "text"],
            ["003", "", "", ""],
        ],
        ["=1+1", "temp"],
    ),
]


def verify_xml_contract(path):
    """Preserve raw cell types, which the frozen value-only oracle cannot inspect."""
    ns = ORIGINAL.NS
    with zipfile.ZipFile(path) as archive:
        targets = {
            node.attrib["Id"]: node.attrib["Target"]
            for node in ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        }
        sheets = ET.fromstring(archive.read("xl/workbook.xml")).findall("s:sheets/s:sheet", ns)
        assert len(sheets) == 3 and {sheet.attrib["name"] for sheet in sheets} == {
            "原始",
            "汇总",
            "异常",
        }, "Expected exactly 原始/汇总/异常 sheets"
        for sheet in sheets:
            target = targets[sheet.attrib[f"{{{ORIGINAL.REL}}}id"]]
            member = posixpath.normpath(
                target.lstrip("/") if target.startswith("/") else "xl/" + target
            )
            root = ET.fromstring(archive.read(member))
            for merged in root.findall("s:mergeCells/s:mergeCell", ns):
                rows = [int(value) for value in re.findall(r"\d+", merged.attrib["ref"])]
                assert rows and min(rows) > 1, f"Merged header in {sheet.attrib['name']}"
            if sheet.attrib["name"] != "原始":
                continue
            for cell in root.findall("s:sheetData/s:row/s:c", ns):
                assert cell.find("s:f", ns) is None, "Unexpected formula in raw data"
                value = cell.findtext("s:v", "", ns) or "".join(
                    node.text or "" for node in cell.findall("s:is//s:t", ns)
                )
                assert not value or cell.attrib.get("t") in {"s", "inlineStr", "str"}, (
                    f"Raw nonempty cell {cell.attrib['r']} must store text"
                )


def verify_workbook(path, headers, records, columns):
    ORIGINAL.verify_workbook(path, headers, records, columns)
    verify_xml_contract(path)


def evaluate(candidate, interpreter, scratch):
    """Exercise only the public CLI; never import candidate implementation or tests."""
    checks = ORIGINAL.evaluate(candidate, interpreter, scratch)
    environment = {
        key: value for key, value in os.environ.items() if key not in {"PYTHONPATH", "PYTHONHOME"}
    }

    def check(name, operation):
        try:
            operation()
        except Exception as exc:
            checks.append(
                {"name": name, "passed": False, "detail": f"{type(exc).__name__}: {exc}"[:2000]}
            )
        else:
            checks.append({"name": name, "passed": True})

    def run(*arguments, input_text=""):
        return subprocess.run(
            [str(interpreter), str(candidate / "lab_data.py"), *map(str, arguments)],
            cwd=candidate,
            input=input_text,
            text=True,
            capture_output=True,
            timeout=20,
            env={**environment, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8"},
        )

    source_hashes = {
        path: hashlib.sha256(path.read_bytes()).hexdigest() for path in scratch.glob("*.csv")
    }
    outputs = [
        scratch / f"{name}.xlsx" for name in ["mixed", "empty-values", "clean", "interactive"]
    ]
    cases = [
        *FORMULA_CASES,
        (
            "utf8_without_bom",
            ["id", "temp", "comment"],
            [["001", "2", "中文"], ["002", "4", "comma, quoted"]],
            ["temp"],
        ),
    ]

    def success(source, output, headers, records, columns):
        result = run(source, "--columns", ",".join(columns), "--output", output)
        assert result.returncode == 0, f"CLI failed: {result.stderr or result.stdout}"
        verify_workbook(output, headers, records, columns)

    for name, headers, records, columns in cases:
        source = scratch / f"{name}.csv"
        output = scratch / f"{name}.xlsx"
        outputs.append(output)
        encoding = "utf-8" if name == "utf8_without_bom" else "utf-8-sig"
        with source.open("w", encoding=encoding, newline="") as stream:
            csv.writer(stream).writerows([headers, *records])
        source_hashes[source] = hashlib.sha256(source.read_bytes()).hexdigest()
        check(
            name, lambda s=source, o=output, h=headers, r=records, c=columns: success(s, o, h, r, c)
        )

    def interactive_rejects(source, output):
        before = output.read_bytes() if output.exists() else None
        result = run(input_text=f"{source}\ntemp\n{output}\n")
        assert result.returncode != 0, "Invalid interactive request reported success"
        assert (result.stderr + result.stdout).strip(), "Failure had no explanation"
        assert output.read_bytes() == before if before is not None else not output.exists(), (
            "Interactive failure changed an existing file or left an output"
        )

    mixed = scratch / "mixed.csv"
    for name, source, output in [
        ("interactive_no_output_overwrite", mixed, scratch / "existing.xlsx"),
        ("interactive_no_input_overwrite", mixed, mixed),
        (
            "interactive_invalid_csv",
            scratch / "duplicate-header.csv",
            scratch / "interactive-invalid.xlsx",
        ),
    ]:
        check(name, lambda s=source, o=output: interactive_rejects(s, o))

    def check_xml_contracts():
        for output in outputs:
            verify_xml_contract(output)

    check("workbook_xml_contract", check_xml_contracts)
    unchanged = all(
        path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == expected
        for path, expected in source_hashes.items()
    )
    checks.append({"name": "followup_inputs_unchanged", "passed": unchanged})
    return checks


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument(
        "--output", type=Path, required=True, help="New result JSON; never overwrite"
    )
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="Install requirements in a clean temporary venv and grade a source copy",
    )
    args = parser.parse_args(argv)
    candidate = args.candidate.resolve()
    output = args.output.absolute()
    if output.exists() or output.is_symlink():
        parser.error(f"Result already exists: {output}")
    if output.resolve().is_relative_to(candidate):
        parser.error("Write grader results outside the candidate directory")
    with tempfile.TemporaryDirectory(prefix="aster-lab-followup-") as directory:
        scratch = Path(directory)
        # Keep a venv interpreter path: resolving its symlink selects the parent Python.
        interpreter = args.python.absolute()
        try:
            if args.rebuild:
                copied = scratch / "candidate"
                shutil.copytree(
                    candidate,
                    copied,
                    ignore=shutil.ignore_patterns(
                        ".venv", "venv", ".git", ".aster", "__pycache__", ".pytest_cache"
                    ),
                )
                candidate = copied
                environment = scratch / "environment"
                subprocess.run(
                    [sys.executable, "-m", "venv", str(environment)],
                    capture_output=True,
                    text=True,
                    timeout=60,
                    check=True,
                )
                interpreter = environment / "bin/python"
                subprocess.run(
                    [
                        str(interpreter),
                        "-m",
                        "pip",
                        "install",
                        "-r",
                        str(candidate / "requirements.txt"),
                    ],
                    cwd=candidate,
                    capture_output=True,
                    text=True,
                    timeout=180,
                    check=True,
                )
            checks = evaluate(candidate, interpreter, scratch)
        except Exception as exc:
            checks = [
                {"name": "setup", "passed": False, "detail": f"{type(exc).__name__}: {exc}"[:2000]}
            ]
    result = {
        "grader_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "original_grader_sha256": ORIGINAL_SHA256,
        "candidate": str(args.candidate.resolve()),
        "clean_rebuild": args.rebuild,
        "checks": checks,
        "passed": bool(checks) and all(row["passed"] for row in checks),
        "manual_gates": [
            "official_docs_read_in_model_visible_tool_output",
            "dependency_install_success_trace",
            "cross_command_dependency_use",
            "aster_self_tests",
            "readme_commands_review",
        ],
    }
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    with output.open("x", encoding="utf-8") as stream:
        stream.write(rendered)
    print(rendered, end="")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
