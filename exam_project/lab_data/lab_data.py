#!/usr/bin/env python3
"""整理实验课程 CSV 数据为三工作表 Excel 报告。"""

from __future__ import annotations

import argparse
import csv
import math
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Sequence

from openpyxl import Workbook

RAW_SHEET = "原始"
SUMMARY_SHEET = "汇总"
ISSUES_SHEET = "异常"
SUMMARY_HEADERS = ["column", "count", "missing", "invalid", "min", "max", "mean"]
ISSUE_HEADERS = ["data_row", "column", "value", "reason"]


class DataError(Exception):
    """用户输入或 CSV 结构/内容错误。"""


def read_csv(path: Path) -> tuple[list[str], list[list[str]]]:
    if not path.exists():
        raise DataError(f"输入文件不存在: {path}")
    if not path.is_file():
        raise DataError(f"输入路径不是文件: {path}")
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.reader(handle)
            try:
                headers = next(reader)
            except StopIteration:
                raise DataError("CSV 为空，必须包含表头和至少一行数据") from None
            if not headers or any(not header.strip() for header in headers):
                raise DataError("CSV 表头不能有空列名")
            if len(set(headers)) != len(headers):
                raise DataError("CSV 表头不能重复")
            rows = list(reader)
    except UnicodeDecodeError as exc:
        raise DataError(f"CSV 不是有效的 UTF-8 文件: {path}") from exc
    except csv.Error as exc:
        raise DataError(f"CSV 格式错误: {exc}") from exc

    if not rows:
        raise DataError("CSV 必须至少包含一行数据")
    expected = len(headers)
    for data_row, row in enumerate(rows, start=1):
        if len(row) != expected:
            raise DataError(
                f"第 {data_row} 行字段数为 {len(row)}，应为 {expected}"
            )
    return headers, rows


def parse_columns(value: str, headers: Sequence[str]) -> list[str]:
    columns = [part.strip() for part in value.split(",")]
    if not columns or any(not column for column in columns):
        raise DataError("数值列名不能为空，并须用逗号分隔")
    if len(set(columns)) != len(columns):
        raise DataError("数值列名不能重复")
    missing = [column for column in columns if column not in headers]
    if missing:
        raise DataError(f"指定的列不存在: {', '.join(missing)}")
    return columns


def analyse(headers: Sequence[str], rows: Sequence[Sequence[str]], columns: Sequence[str]):
    positions = {header: headers.index(header) for header in columns}
    summary = []
    issues = []
    for column in columns:
        values: list[float] = []
        missing = 0
        invalid = 0
        position = positions[column]
        for data_row, row in enumerate(rows, start=1):
            raw = row[position]
            stripped = raw.strip()
            if not stripped:
                missing += 1
                issues.append([data_row, column, raw, "missing"])
                continue
            try:
                number = float(stripped)
            except ValueError:
                invalid += 1
                issues.append([data_row, column, raw, "invalid"])
                continue
            if not math.isfinite(number):
                invalid += 1
                issues.append([data_row, column, raw, "invalid"])
                continue
            values.append(number)
        summary.append([
            column,
            len(values),
            missing,
            invalid,
            min(values) if values else None,
            max(values) if values else None,
            sum(values) / len(values) if values else None,
        ])
    return summary, issues


def _write_text_cell(sheet, row: int, column: int, value: str) -> None:
    """Write source text explicitly as text, including formula-looking strings."""
    cell = sheet.cell(row=row, column=column)
    cell.value = value if value != "" else None
    if value != "":
        cell.data_type = "s"


def write_workbook(path: Path, headers, rows, summary, issues) -> None:
    workbook = Workbook()
    raw_sheet = workbook.active
    raw_sheet.title = RAW_SHEET
    summary_sheet = workbook.create_sheet(SUMMARY_SHEET)
    issues_sheet = workbook.create_sheet(ISSUES_SHEET)

    for column, header in enumerate(headers, start=1):
        _write_text_cell(raw_sheet, 1, column, header)
    for row_number, source_row in enumerate(rows, start=2):
        for column, value in enumerate(source_row, start=1):
            _write_text_cell(raw_sheet, row_number, column, value)

    for column, value in enumerate(SUMMARY_HEADERS, start=1):
        _write_text_cell(summary_sheet, 1, column, value)
    for row_number, source_row in enumerate(summary, start=2):
        for column, value in enumerate(source_row, start=1):
            if isinstance(value, str):
                _write_text_cell(summary_sheet, row_number, column, value)
            else:
                summary_sheet.cell(row=row_number, column=column).value = value
    for column, value in enumerate(ISSUE_HEADERS, start=1):
        _write_text_cell(issues_sheet, 1, column, value)
    for row_number, source_row in enumerate(issues, start=2):
        for column, value in enumerate(source_row, start=1):
            if isinstance(value, str):
                _write_text_cell(issues_sheet, row_number, column, value)
            else:
                issues_sheet.cell(row=row_number, column=column).value = value

    # Save beside the destination and replace only after a complete save.
    destination = path.resolve()
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        workbook.save(temporary)
        # Create the destination exclusively so a concurrent or existing file is never replaced.
        destination_fd = os.open(
            destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666
        )
        try:
            with temporary.open("rb") as source, os.fdopen(destination_fd, "wb") as target:
                shutil.copyfileobj(source, target)
        except Exception:
            try:
                os.close(destination_fd)
            except OSError:
                pass
            destination.unlink(missing_ok=True)
            raise
        temporary.unlink()
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def convert(input_path: str, columns_text: str, output_path: str) -> None:
    input_file = Path(input_path).expanduser()
    output_file = Path(output_path).expanduser()
    if input_file.resolve() == output_file.resolve():
        raise DataError("输入路径和输出路径不能相同")
    if output_file.exists():
        raise DataError(f"输出文件已存在，不会覆盖: {output_file}")
    headers, rows = read_csv(input_file)
    columns = parse_columns(columns_text, headers)
    summary, issues = analyse(headers, rows, columns)
    try:
        write_workbook(output_file, headers, rows, summary, issues)
    except FileNotFoundError as exc:
        raise DataError(f"输出目录不存在: {output_file.parent}") from exc
    except OSError as exc:
        raise DataError(f"无法写入输出文件: {exc}") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="将实验课程 CSV 整理为包含原始、汇总、异常三个工作表的 Excel 文件。"
    )
    parser.add_argument("input", nargs="?", help="输入 UTF-8 CSV 路径")
    parser.add_argument("--columns", help="逗号分隔的数值列名，例如 temp,ph")
    parser.add_argument("--output", help="输出 XLSX 路径（必须不存在）")
    return parser


def interactive_values() -> tuple[str, str, str]:
    return (
        input("输入 CSV 路径: ").strip(),
        input("数值列名（逗号分隔）: ").strip(),
        input("输出 Excel 路径: ").strip(),
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.input is None:
            if args.columns is not None or args.output is not None:
                parser.error("交互模式不能与 --columns 或 --output 混用")
            input_path, columns, output_path = interactive_values()
        else:
            if args.columns is None or args.output is None:
                parser.error("命令模式必须同时提供 --columns 和 --output")
            input_path, columns, output_path = args.input, args.columns, args.output
        if not input_path or not columns or not output_path:
            raise DataError("输入路径、数值列名和输出路径都不能为空")
        convert(input_path, columns, output_path)
    except (DataError, EOFError, KeyboardInterrupt) as exc:
        message = "操作已取消" if isinstance(exc, KeyboardInterrupt) else str(exc)
        print(f"错误: {message}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"错误: 无法访问文件: {exc}", file=sys.stderr)
        return 2
    print(f"已生成 Excel: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
