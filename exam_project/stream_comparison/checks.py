"""Independent operator checks, never copied into a candidate workspace during generation."""

import json
import subprocess
import sys
from pathlib import Path


def run_cli(*arguments):
    return subprocess.run(
        [sys.executable, "linecounts.py", *map(str, arguments)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=5,
    )


def invoke(tmp_path, data):
    source = tmp_path / "input with spaces.txt"
    source.write_bytes(data)
    result = run_cli(source)
    assert source.read_bytes() == data, "input was modified"
    return result


def success(result, expected):
    assert result.returncode == 0 and result.stderr == ""
    assert result.stdout.endswith("\n")
    actual = json.loads(result.stdout)
    assert actual == expected
    assert list(actual) == sorted(expected)
    assert all(type(value) is int for value in actual.values())


def failure(result):
    assert result.returncode == 2 and result.stdout == ""
    assert result.stderr.strip() and "Traceback" not in result.stderr


def test_required_artifacts():
    assert Path("linecounts.py").is_file()
    assert Path("README.md").is_file() and Path("README.md").read_text().strip()


def test_parameters():
    failure(run_cli())
    failure(run_cli("one.txt", "two.txt"))
    failure(run_cli("--unknown-option"))


def test_empty_and_whitespace_input(tmp_path):
    for data in (b"", b"\n\r\n \t\n"):
        success(invoke(tmp_path, data), {})


def test_counts_sorting_case_and_internal_whitespace(tmp_path):
    success(
        invoke(tmp_path, b" zebra \nAlpha\nzebra\nalpha\n two words \ntwo  words\n\tAlpha\t\n"),
        {"Alpha": 2, "alpha": 1, "two  words": 1, "two words": 1, "zebra": 2},
    )


def test_chinese_duplicates_and_unicode_whitespace(tmp_path):
    result = invoke(tmp_path, "\u3000中文\u3000\n重复\n中文\n\t重复 \n中文\n".encode())
    success(result, {"中文": 3, "重复": 2})
    assert "中文" in result.stdout and "重复" in result.stdout


def test_crlf_and_missing_final_newline(tmp_path):
    success(invoke(tmp_path, b"beta\r\n alpha \r\nbeta"), {"alpha": 1, "beta": 2})


def test_file_and_encoding_errors(tmp_path):
    failure(run_cli(tmp_path / "missing.txt"))
    failure(run_cli(tmp_path))
    failure(invoke(tmp_path, b"valid\n\xff\xfe\n"))
