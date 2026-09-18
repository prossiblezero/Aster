"""Independent black-box acceptance; never available during candidate generation."""

import json
import subprocess
import sys
from pathlib import Path


def cli(*arguments):
    return subprocess.run(
        [sys.executable, "uniqwords.py", *map(str, arguments)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=5,
    )


def input_file(tmp_path, data):
    path = tmp_path / "input with spaces.txt"
    path.write_bytes(data)
    return path


def success(result, expected):
    assert result.returncode == 0 and result.stderr == ""
    assert result.stdout.endswith("\n")
    actual = json.loads(result.stdout)
    assert actual == expected and list(actual) == sorted(expected)
    assert all(type(value) is int for value in actual.values())
    assert "\\u" not in result.stdout


def counts(tmp_path, data, expected, *options):
    path = input_file(tmp_path, data)
    success(cli(*options, path), expected)
    assert path.read_bytes() == data


def failure(result):
    assert result.returncode == 2 and result.stdout == ""
    assert result.stderr.strip() and "Traceback" not in result.stderr


def test_required_artifacts_and_self_tests():
    assert Path("uniqwords.py").is_file()
    assert Path("README.md").is_file() and Path("README.md").read_text().strip()
    assert any(Path(".").glob("**/test*.py"))


def test_parameter_errors():
    failure(cli())
    failure(cli("one.txt", "two.txt"))
    failure(cli("--unknown-option"))


def test_empty_and_whitespace(tmp_path):
    for data in (b"", " \t\r\n\u3000\u2003".encode()):
        counts(tmp_path, data, {})


def test_case_sensitive_sorting_and_punctuation(tmp_path):
    counts(
        tmp_path,
        b"Alpha alpha Alpha zebra zebra, zebra\nalpha!",
        {
            "Alpha": 2,
            "alpha": 1,
            "alpha!": 1,
            "zebra": 2,
            "zebra,": 1,
        },
    )


def test_unicode_words_and_whitespace(tmp_path):
    counts(tmp_path, "中文\u3000重复\n中文\t重复\u2003中文".encode(), {"中文": 3, "重复": 2})


def test_no_unicode_normalization(tmp_path):
    counts(tmp_path, "é e\u0301 é É".encode(), {"é": 2, "e\u0301": 1, "É": 1})


def test_crlf_no_final_newline_and_spaced_filename(tmp_path):
    counts(tmp_path, b"beta\r\nalpha\r\nbeta\talpha gamma", {"alpha": 2, "beta": 2, "gamma": 1})


def test_file_and_decoding_errors(tmp_path):
    failure(cli(tmp_path / "missing.txt"))
    failure(cli(tmp_path))
    path = input_file(tmp_path, b"valid\n\xff\xfe")
    failure(cli(path))
    assert path.read_bytes() == b"valid\n\xff\xfe"
