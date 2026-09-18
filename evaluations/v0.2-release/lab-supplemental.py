"""One additional contract check; keeps the frozen 16-check result untouched."""

import argparse
import json
import posixpath
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--python", required=True, type=Path)
    args = parser.parse_args()
    namespace = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    relation = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    with tempfile.TemporaryDirectory(prefix="aster-lab-formula-") as directory:
        root = Path(directory)
        source, output = root / "formula.csv", root / "report.xlsx"
        source.write_text("measurement\n=1+1\n", encoding="utf-8")
        command = [
            str(args.python.absolute()),
            str(args.candidate.resolve() / "lab_data.py"),
            str(source),
            "--columns",
            "measurement",
            "--output",
            str(output),
        ]
        process = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=20)
        detail = {
            "case": "formula_text_in_selected_numeric_column",
            "csv": source.read_text(),
            "exit_code": process.returncode,
            "stdout": process.stdout,
            "stderr": process.stderr,
            "passed": False,
            "sheets": {},
        }
        if process.returncode == 0 and output.exists():
            with zipfile.ZipFile(output) as archive:
                targets = {
                    node.attrib["Id"]: node.attrib["Target"]
                    for node in ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
                }
                for sheet in ET.fromstring(archive.read("xl/workbook.xml")).findall(
                    "s:sheets/s:sheet", namespace
                ):
                    target = targets[sheet.attrib[f"{{{relation}}}id"]]
                    member = posixpath.normpath(
                        target.lstrip("/") if target.startswith("/") else "xl/" + target
                    )
                    xml = archive.read(member).decode("utf-8")
                    cells = []
                    for cell in ET.fromstring(xml).findall("s:sheetData/s:row/s:c", namespace):
                        cells.append(
                            {
                                "coordinate": cell.attrib["r"],
                                "type": cell.attrib.get("t"),
                                "formula": cell.findtext("s:f", None, namespace),
                                "inline_text": "".join(
                                    node.text or "" for node in cell.findall("s:is//s:t", namespace)
                                ),
                            }
                        )
                    detail["sheets"][sheet.attrib["name"]] = {"cells": cells, "xml": xml}
            cells = detail["sheets"].get("异常", {}).get("cells", [])
            value = next((cell for cell in cells if cell["coordinate"] == "C2"), {})
            detail["passed"] = value.get("formula") is None and value.get("inline_text") == "=1+1"
            detail["expected"] = "异常!C2 retains =1+1 as literal text, never an Excel formula"
            detail["actual_anomaly_value"] = value
        print(json.dumps(detail, ensure_ascii=False, indent=2))
        return 0 if detail["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
