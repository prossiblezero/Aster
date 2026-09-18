"""Positive/negative fixtures validate the grader without implementing a candidate."""

import importlib.util
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "lab_grader", Path(__file__).with_name("lab-grader.py")
)
GRADER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GRADER)


def workbook(path, sheets, *, shared=False):
    namespace = GRADER.NS["s"]
    root = ET.Element(f"{{{namespace}}}workbook")
    listing = ET.SubElement(root, f"{{{namespace}}}sheets")
    relationships = ET.Element("Relationships")
    strings = []
    with zipfile.ZipFile(path, "w") as archive:
        for sheet_number, (name, rows) in enumerate(sheets.items(), 1):
            ET.SubElement(
                listing,
                f"{{{namespace}}}sheet",
                name=name,
                **{f"{{{GRADER.REL}}}id": f"rel{sheet_number}"},
            )
            ET.SubElement(
                relationships,
                "Relationship",
                Id=f"rel{sheet_number}",
                Target=f"/xl/worksheets/data{sheet_number}.xml",
            )
            sheet = ET.Element(f"{{{namespace}}}worksheet")
            data = ET.SubElement(sheet, f"{{{namespace}}}sheetData")
            for row_number, values in enumerate(rows, 1):
                row = ET.SubElement(data, f"{{{namespace}}}row", r=str(row_number))
                for column, value in enumerate(values):
                    if value == "":
                        continue  # Real writers often omit empty cells entirely.
                    cell = ET.SubElement(
                        row,
                        f"{{{namespace}}}c",
                        r=f"{chr(65 + column)}{row_number}",
                        t="s" if shared else "inlineStr",
                    )
                    if shared:
                        strings.append(str(value))
                        ET.SubElement(cell, f"{{{namespace}}}v").text = str(len(strings) - 1)
                    else:
                        inline = ET.SubElement(cell, f"{{{namespace}}}is")
                        ET.SubElement(inline, f"{{{namespace}}}t").text = str(value)
            archive.writestr(f"xl/worksheets/data{sheet_number}.xml", ET.tostring(sheet))
        archive.writestr("xl/workbook.xml", ET.tostring(root))
        archive.writestr("xl/_rels/workbook.xml.rels", ET.tostring(relationships))
        if shared:
            root = ET.Element(f"{{{namespace}}}sst")
            for value in strings:
                item = ET.SubElement(root, f"{{{namespace}}}si")
                ET.SubElement(item, f"{{{namespace}}}t").text = value
            archive.writestr("xl/sharedStrings.xml", ET.tostring(root))


def fixture():
    headers = ["sample", "temp"]
    records = [["001", "2"], ["002", "4"], ["003", ""], ["004", "NaN"]]
    return (
        headers,
        records,
        {
            "原始": [headers, *records],
            "汇总": [GRADER.SUMMARY, ["temp", 2, 1, 1, 2, 4, 3]],
            "异常": [GRADER.ANOMALIES, [3, "temp", "", "missing"], [4, "temp", "NaN", "invalid"]],
        },
    )


@pytest.mark.parametrize("shared", [False, True])
def test_accepts_equivalent_valid_workbook(tmp_path, shared):
    headers, records, sheets = fixture()
    # Column ordering and anomaly row ordering are intentionally unconstrained.
    sheets["汇总"] = [list(reversed(row)) for row in sheets["汇总"]]
    sheets["异常"] = [sheets["异常"][0], *reversed(sheets["异常"][1:])]
    path = tmp_path / "good.xlsx"
    workbook(path, sheets, shared=shared)
    GRADER.verify_workbook(path, headers, records, ["temp"])


@pytest.mark.parametrize(
    "mutation",
    ["wrong_mean", "lost_anomaly", "lost_leading_zero", "empty_result", "duplicate_summary"],
)
def test_rejects_wrong_or_missing_results(tmp_path, mutation):
    headers, records, sheets = fixture()
    if mutation == "wrong_mean":
        sheets["汇总"][1][-1] = 0
    elif mutation == "lost_anomaly":
        sheets["异常"].pop()
    elif mutation == "lost_leading_zero":
        sheets["原始"] = [row.copy() for row in sheets["原始"]]
        sheets["原始"][1][0] = "1"
    elif mutation == "empty_result":
        sheets = {}
    else:
        sheets["汇总"].append(sheets["汇总"][1])
    path = tmp_path / "bad.xlsx"
    workbook(path, sheets)
    with pytest.raises(AssertionError):
        GRADER.verify_workbook(path, headers, records, ["temp"])


def test_rejects_missing_candidate(tmp_path):
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    scratch = tmp_path / "checks"
    scratch.mkdir()
    import sys

    checks = GRADER.evaluate(candidate, Path(sys.executable), scratch)
    assert checks and not all(item["passed"] for item in checks)
    assert not next(item for item in checks if item["name"] == "mixed_csv_workbook")["passed"]


def test_accepts_missing_aggregates_only_when_no_valid_values(tmp_path):
    headers = ["a", "b"]
    records = [["", "-4"], ["bad", "-2"], ["nan", ""]]
    sheets = {
        "原始": [headers, *records],
        "汇总": [
            GRADER.SUMMARY,
            ["a", 0, 1, 2, "", "", ""],
            ["b", 2, 1, 0, -4, -2, -3],
        ],
        "异常": [
            GRADER.ANOMALIES,
            [1, "a", "", "missing"],
            [2, "a", "bad", "invalid"],
            [3, "a", "nan", "invalid"],
            [3, "b", "", "missing"],
        ],
    }
    path = tmp_path / "empty-aggregates.xlsx"
    workbook(path, sheets)
    GRADER.verify_workbook(path, headers, records, ["a", "b"])
    sheets["汇总"][1][-1] = 0
    workbook(path, sheets)
    with pytest.raises(AssertionError):
        GRADER.verify_workbook(path, headers, records, ["a", "b"])


def test_independent_expected_counts_classify_nonfinite_and_empty():
    summary, anomalies = GRADER.expected_tables(
        ["v"], [[""], [" "], ["bad"], ["nan"], ["inf"], ["-inf"], ["-2"], ["4"]], ["v"]
    )
    assert summary == [["v", 2, 2, 4, -2, 4, 1]]
    assert len(anomalies) == 6
