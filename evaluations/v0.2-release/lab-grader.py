"""Independent public-CLI acceptance; no candidate imports or model requests."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import posixpath
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
SUMMARY = ["column", "count", "missing", "invalid", "min", "max", "mean"]
ANOMALIES = ["data_row", "column", "value", "reason"]


def read_workbook(path):
    """Read values independent of openpyxl, including shared/inline string workbooks."""
    with zipfile.ZipFile(path) as archive:
        shared = []
        if "xl/sharedStrings.xml" in archive.namelist():
            shared = [
                "".join(node.itertext())
                for node in ET.fromstring(archive.read("xl/sharedStrings.xml"))
            ]
        targets = {
            node.attrib["Id"]: node.attrib["Target"]
            for node in ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        }
        result = {}
        for sheet in ET.fromstring(archive.read("xl/workbook.xml")).findall("s:sheets/s:sheet", NS):
            target = targets[sheet.attrib[f"{{{REL}}}id"]]
            member = posixpath.normpath(
                target.lstrip("/") if target.startswith("/") else "xl/" + target
            )
            rows = []
            for row in ET.fromstring(archive.read(member)).findall("s:sheetData/s:row", NS):
                cells = []
                for cell in row.findall("s:c", NS):
                    coordinate = cell.attrib["r"]
                    column = 0
                    for letter in coordinate:
                        if letter.isalpha():
                            column = column * 26 + ord(letter.upper()) - ord("A") + 1
                    while len(cells) < column:
                        cells.append("")
                    assert cell.find("s:f", NS) is None, (
                        "Unexpected formula instead of a stored value"
                    )
                    value = cell.findtext("s:v", "", NS)
                    if cell.attrib.get("t") == "inlineStr":
                        value = "".join(node.text or "" for node in cell.findall("s:is//s:t", NS))
                    elif cell.attrib.get("t") == "s":
                        value = shared[int(value)]
                    cells[column - 1] = value
                row_number = int(row.attrib["r"])
                while len(rows) < row_number:
                    rows.append([])
                rows[row_number - 1] = cells
            assert sheet.attrib["name"] not in result, "Duplicate worksheet name"
            result[sheet.attrib["name"]] = rows
        return result


def table(rows, fields):
    assert rows and len(rows[0]) == len(fields) and set(rows[0]) == set(fields), (
        "Wrong table headers"
    )
    result = []
    for row in rows[1:]:
        assert len(row) <= len(fields), "Unexpected extra columns"
        result.append(dict(zip(rows[0], row + [""] * (len(fields) - len(row)), strict=True)))
    return result


def expected_tables(headers, records, columns):
    summary, anomalies = [], []
    for name in columns:
        values, missing, invalid = [], 0, 0
        index = headers.index(name)
        for row_number, row in enumerate(records, 1):
            value, reason = row[index], ""
            if not value.strip():
                missing += 1
                reason = "missing"
            else:
                try:
                    number = float(value)
                except ValueError:
                    number = math.nan
                if not math.isfinite(number):
                    invalid += 1
                    reason = "invalid"
                else:
                    values.append(number)
            if reason:
                anomalies.append((str(row_number), name, value, reason))
        summary.append(
            [name, len(values), missing, invalid]
            + ([min(values), max(values), sum(values) / len(values)] if values else ["", "", ""])
        )
    return summary, sorted(anomalies)


def verify_workbook(path, headers, records, columns):
    sheets = read_workbook(path)
    assert set(sheets) == {"原始", "汇总", "异常"}, "Expected exactly 原始/汇总/异常 sheets"
    raw = [row + [""] * (len(headers) - len(row)) for row in sheets["原始"]]
    assert raw == [headers, *records], "Raw input changed, reordered or omitted"
    expected, anomalies = expected_tables(headers, records, columns)
    actual = table(sheets["汇总"], SUMMARY)
    assert len(actual) == len(columns), "Summary must contain each selected column exactly once"
    by_name = {row["column"]: row for row in actual}
    assert set(by_name) == set(columns), "Missing/duplicate/unselected summary column"
    for row in expected:
        for field, value in zip(SUMMARY[1:], row[1:], strict=True):
            observed = by_name[row[0]][field]
            assert (
                observed == ""
                if value == ""
                else math.isclose(float(observed), value, rel_tol=1e-9, abs_tol=1e-9)
            ), f"Wrong {row[0]}.{field}"
    observed = [
        tuple(row[field] for field in ANOMALIES) for row in table(sheets["异常"], ANOMALIES)
    ]
    assert sorted(observed) == anomalies, "Anomaly detail missing, duplicated or incorrect"


def evaluate(candidate, interpreter, scratch):
    checks = []
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

    def run(*arguments, input_text=None):
        return subprocess.run(
            [str(interpreter), str(candidate / "lab_data.py"), *map(str, arguments)],
            cwd=candidate,
            input=input_text or "",
            text=True,
            capture_output=True,
            timeout=20,
            env={**environment, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8"},
        )

    def require(condition, message):
        assert condition, message

    def artifact_check():
        require((candidate / "README.md").is_file(), "README.md missing")
        requirements = (candidate / "requirements.txt").read_text().lower()
        require(
            any(line.strip().startswith("openpyxl") for line in requirements.splitlines()),
            "Declare openpyxl in requirements.txt",
        )
        require(
            any(
                path.is_file()
                for path in candidate.rglob("*.csv")
                if not any(part.startswith(".") for part in path.relative_to(candidate).parts)
            ),
            "Fictional CSV example missing",
        )

    check("delivery_files", artifact_check)
    check("help", lambda: require(run("--help").returncode == 0, "--help failed"))
    source_hashes = {}

    def source(name, headers, records):
        path = scratch / (name + ".csv")
        with path.open("w", encoding="utf-8-sig", newline="") as stream:
            csv.writer(stream).writerows([headers, *records])
        source_hashes[path] = hashlib.sha256(path.read_bytes()).hexdigest()
        return path

    headers = ["run_id", "temp", "ph", "comment"]
    records = [
        ["001", "20", "7", "正常"],
        ["002", "", "8", "comma, quoted"],
        ["003", "bad", "9", "非法"],
        ["004", "30", " ", " 空白 "],
        ["005", "40", "NaN", "非有限"],
        ["006", "-10", "inf", "=1+1"],
        ["007", "1e2", "-inf", "科学计数"],
    ]
    mixed = source("mixed", headers, records)

    def success(name, source_path, field_names, values, columns, *, interactive=False):
        output = scratch / (name + ".xlsx")
        args = [source_path, "--columns", ",".join(columns), "--output", output]
        result = (
            run(input_text=f"{source_path}\n{','.join(columns)}\n{output}\n")
            if interactive
            else run(*args)
        )
        require(result.returncode == 0, f"CLI failed: {result.stderr or result.stdout}")
        verify_workbook(output, field_names, values, columns)

    check("mixed_csv_workbook", lambda: success("mixed", mixed, headers, records, ["temp", "ph"]))
    empty_rows = [["", "-4"], ["bad", "-2"], ["nan", ""]]
    empty_values = source("all-invalid", ["a", "b"], empty_rows)
    check(
        "column_without_valid_values",
        lambda: success("empty-values", empty_values, ["a", "b"], empty_rows, ["a", "b"]),
    )
    clean_rows = [["2.5", "alpha"], ["7.5", "beta"]]
    clean = source("clean", ["value", "label"], clean_rows)
    check(
        "clean_csv_empty_anomalies",
        lambda: success("clean", clean, ["value", "label"], clean_rows, ["value"]),
    )
    check(
        "interactive_cli",
        lambda: success("interactive", mixed, headers, records, ["temp", "ph"], interactive=True),
    )

    def rejects(name, source_path, columns="temp", *, output=None):
        target = output or (scratch / (name + ".xlsx"))
        before = target.read_bytes() if target.exists() else None
        result = run(source_path, "--columns", columns, "--output", target)
        require(result.returncode != 0, "Invalid request reported success")
        require(bool((result.stderr + result.stdout).strip()), "Failure had no explanation")
        require(
            target.read_bytes() == before if before is not None else not target.exists(),
            "Failure changed existing file or left an output",
        )

    sentinel = scratch / "existing.xlsx"
    sentinel.write_bytes(b"existing user output must survive")
    check("no_output_overwrite", lambda: rejects("existing", mixed, output=sentinel))
    check("no_input_overwrite", lambda: rejects("same-path", mixed, output=mixed))
    check("missing_file", lambda: rejects("missing", scratch / "missing.csv"))
    check("unknown_column", lambda: rejects("unknown", mixed, "does_not_exist"))
    for name, body in [
        ("empty-file", ""),
        ("unequal-fields", "temp,ph\n1,2,3\n"),
        ("duplicate-header", "temp,temp\n1,2\n"),
        ("blank-header", "temp,\n1,2\n"),
        ("header-only", "temp,ph\n"),
    ]:
        path = scratch / (name + ".csv")
        path.write_text(body, encoding="utf-8")
        source_hashes[path] = hashlib.sha256(path.read_bytes()).hexdigest()
        check(name, lambda n=name, p=path: rejects(n, p))
    check(
        "all_inputs_unchanged",
        lambda: require(
            all(
                path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == expected
                for path, expected in source_hashes.items()
            ),
            "A source file changed",
        ),
    )
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="Install requirements in a clean temporary venv and grade a source copy",
    )
    args = parser.parse_args()
    candidate = args.candidate.resolve()
    with tempfile.TemporaryDirectory(prefix="aster-lab-acceptance-") as directory:
        scratch = Path(directory)
        # Resolving a venv's python symlink would silently select its parent environment.
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
        "candidate": str(args.candidate.resolve()),
        "clean_rebuild": args.rebuild,
        "checks": checks,
        "passed": bool(checks) and all(row["passed"] for row in checks),
        "manual_gates": [
            "official_docs_and_install_trace",
            "cross_command_dependency_use",
            "aster_self_tests",
            "readme_commands_review",
        ],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
