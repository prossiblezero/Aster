"""Oracle self-tests use only handcrafted XML, never a candidate or a model."""

import importlib.util
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "followup_lab_grader", Path(__file__).with_name("grader.py")
)
GRADER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GRADER)
SPEC = importlib.util.spec_from_file_location(
    "original_lab_grader_fixtures", GRADER.ORIGINAL_PATH.with_name("test_lab_grader.py")
)
FIXTURES = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FIXTURES)


def formula_fixture():
    headers = ["run_id", "=1+1", "temp", "=note"]
    records = [
        ["001", "2", "=3+3", "=text"],
        ["002", "=1+1", "4", "text"],
        ["003", "", "", ""],
        ["004", "  =1+1  ", "-2", " padded "],
    ]
    sheets = {
        "原始": [headers.copy(), *[row.copy() for row in records]],
        "汇总": [
            GRADER.ORIGINAL.SUMMARY.copy(),
            ["=1+1", 1, 1, 2, 2, 2, 2],
            ["temp", 2, 1, 1, -2, 4, 1],
        ],
        "异常": [
            GRADER.ORIGINAL.ANOMALIES.copy(),
            [1, "temp", "=3+3", "invalid"],
            [2, "=1+1", "=1+1", "invalid"],
            [3, "=1+1", "", "missing"],
            [3, "temp", "", "missing"],
            [4, "=1+1", "  =1+1  ", "invalid"],
        ],
    }
    return headers, records, sheets


def modify_sheet(path, sheet_number, operation):
    with zipfile.ZipFile(path) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    name = f"xl/worksheets/data{sheet_number}.xml"
    root = ET.fromstring(members[name])
    operation(root)
    members[name] = ET.tostring(root)
    with zipfile.ZipFile(path, "w") as archive:
        for member, content in members.items():
            archive.writestr(member, content)


def replace_with_formula(path, sheet_number, coordinate):
    """Model the serialization defect without importing a spreadsheet library."""

    def replace(root):
        cell = root.find(f"s:sheetData/s:row/s:c[@r='{coordinate}']", GRADER.ORIGINAL.NS)
        assert cell is not None
        for child in list(cell):
            cell.remove(child)
        cell.attrib.pop("t", None)
        ET.SubElement(cell, f"{{{GRADER.ORIGINAL.NS['s']}}}f").text = "1+1"
        # A cached value cannot make a formula a literal input value.
        ET.SubElement(cell, f"{{{GRADER.ORIGINAL.NS['s']}}}v").text = "2"

    modify_sheet(path, sheet_number, replace)


@pytest.mark.parametrize("shared", [False, True])
@pytest.mark.parametrize("reordered", [False, True])
def test_literal_formulas_and_dynamic_names_pass(tmp_path, shared, reordered):
    headers, records, sheets = formula_fixture()
    if reordered:
        # Both table column order and summary/anomaly row order are unconstrained.
        for name in ["汇总", "异常"]:
            sheets[name] = [list(reversed(row)) for row in sheets[name]]
            sheets[name] = [sheets[name][0], *reversed(sheets[name][1:])]
    path = tmp_path / "valid.xlsx"
    FIXTURES.workbook(path, sheets, shared=shared)
    GRADER.verify_workbook(path, headers, records, ["=1+1", "temp"])


@pytest.mark.parametrize("shared", [False, True])
def test_original_selected_value_bug_is_rejected(tmp_path, shared):
    headers, records = ["measurement"], [["=1+1"]]
    sheets = {
        "原始": [headers, *records],
        "汇总": [GRADER.ORIGINAL.SUMMARY, ["measurement", 0, 0, 1, "", "", ""]],
        "异常": [GRADER.ORIGINAL.ANOMALIES, [1, "measurement", "=1+1", "invalid"]],
    }
    path = tmp_path / "original-defect.xlsx"
    FIXTURES.workbook(path, sheets, shared=shared)
    GRADER.verify_workbook(path, headers, records, ["measurement"])
    replace_with_formula(path, 3, "C2")
    with pytest.raises(AssertionError, match="Unexpected formula"):
        GRADER.verify_workbook(path, headers, records, ["measurement"])


@pytest.mark.parametrize("shared", [False, True])
@pytest.mark.parametrize(
    ("sheet_number", "coordinate"),
    [(1, "B1"), (1, "D1"), (1, "B3"), (1, "D2"), (2, "A2"), (3, "B3"), (3, "C3")],
)
def test_formula_serialization_rejected_in_all_text_positions(
    tmp_path, shared, sheet_number, coordinate
):
    headers, records, sheets = formula_fixture()
    path = tmp_path / "formula.xlsx"
    FIXTURES.workbook(path, sheets, shared=shared)
    replace_with_formula(path, sheet_number, coordinate)
    with pytest.raises(AssertionError, match="Unexpected formula"):
        GRADER.verify_workbook(path, headers, records, ["=1+1", "temp"])


@pytest.mark.parametrize(
    "mutation", ["anomaly_text", "trimmed_text", "missing_anomaly", "dynamic_name", "leading_zero"]
)
def test_literal_value_changes_or_omissions_fail(tmp_path, mutation):
    headers, records, sheets = formula_fixture()
    if mutation == "anomaly_text":
        sheets["异常"][2][2] = "'=1+1"
    elif mutation == "trimmed_text":
        sheets["异常"][-1][2] = "=1+1"
    elif mutation == "missing_anomaly":
        sheets["异常"].pop(2)
    elif mutation == "dynamic_name":
        sheets["汇总"][1][0] = "2"
    else:
        sheets["原始"][1][0] = "1"
    path = tmp_path / "changed.xlsx"
    FIXTURES.workbook(path, sheets)
    with pytest.raises(AssertionError):
        GRADER.verify_workbook(path, headers, records, ["=1+1", "temp"])


@pytest.mark.parametrize("cell_type", ["str", "n", None])
def test_raw_numeric_looking_values_must_still_be_text(tmp_path, cell_type):
    headers, records, sheets = formula_fixture()
    path = tmp_path / "cell-types.xlsx"
    FIXTURES.workbook(path, sheets)

    def replace(root):
        cell = root.find("s:sheetData/s:row/s:c[@r='B2']", GRADER.ORIGINAL.NS)
        for child in list(cell):
            cell.remove(child)
        cell.attrib.pop("t", None)
        if cell_type:
            cell.attrib["t"] = cell_type
        ET.SubElement(cell, f"{{{GRADER.ORIGINAL.NS['s']}}}v").text = "2"

    modify_sheet(path, 1, replace)
    # The old oracle sees exactly the same string regardless of XML cell type.
    GRADER.ORIGINAL.verify_workbook(path, headers, records, ["=1+1", "temp"])
    if cell_type == "str":
        GRADER.verify_workbook(path, headers, records, ["=1+1", "temp"])
    else:
        with pytest.raises(AssertionError, match="must store text"):
            GRADER.verify_workbook(path, headers, records, ["=1+1", "temp"])


@pytest.mark.parametrize("sheet_number", [1, 2, 3])
def test_merged_headers_are_rejected_on_every_sheet(tmp_path, sheet_number):
    headers, records, sheets = formula_fixture()
    path = tmp_path / "merged.xlsx"
    FIXTURES.workbook(path, sheets)

    def merge(root):
        namespace = GRADER.ORIGINAL.NS["s"]
        merges = ET.SubElement(root, f"{{{namespace}}}mergeCells")
        ET.SubElement(merges, f"{{{namespace}}}mergeCell", ref="A1:B1")

    modify_sheet(path, sheet_number, merge)
    with pytest.raises(AssertionError, match="Merged header"):
        GRADER.verify_workbook(path, headers, records, ["=1+1", "temp"])


def test_existing_result_is_never_overwritten_or_candidate_run(tmp_path, monkeypatch):
    output = tmp_path / "result.json"
    output.write_text("preserved evidence\n", encoding="utf-8")

    def unexpected(*args):
        pytest.fail("Preflight must reject existing evidence before evaluating a candidate")

    monkeypatch.setattr(GRADER, "evaluate", unexpected)
    with pytest.raises(SystemExit) as caught:
        GRADER.main([str(tmp_path / "unused-candidate"), "--output", str(output)])
    assert caught.value.code == 2
    assert output.read_text(encoding="utf-8") == "preserved evidence\n"


def test_cli_can_load_frozen_grader_from_an_unrelated_working_directory(tmp_path):
    result = subprocess.run(
        [sys.executable, str(Path(GRADER.__file__).resolve()), "--help"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert "--rebuild" in result.stdout and "--output" in result.stdout
