"""Independent operator-owned checks, hidden during candidate generation."""

import json
import subprocess
import sys
from pathlib import Path


def cli(*arguments):
    return subprocess.run(
        [sys.executable, "eventcount.py", *map(str, arguments)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=5,
    )


def success(result, categories, records, total):
    assert result.returncode == 0 and result.stderr == ""
    assert result.stdout.endswith("\n") and len(result.stdout.splitlines()) == 1
    actual = json.loads(result.stdout)
    assert actual == {
        "categories": [
            {"category": key, "count": value} for key, value in sorted(categories.items())
        ],
        "records": records,
        "total": total,
    }
    assert type(actual["records"]) is int and type(actual["total"]) is int
    assert all(type(item["count"]) is int for item in actual["categories"])


def failure(result):
    assert result.returncode == 2 and result.stdout == ""
    assert result.stderr.strip() and "Traceback" not in result.stderr


def test_required_artifacts_and_tests_exist():
    assert Path("eventcount.py").is_file()
    assert Path("README.md").is_file() and Path("README.md").read_text().strip()
    assert any(path.name.startswith("test_") for path in Path(".").rglob("*.py"))


def test_aggregate_unicode_and_ignore_extra_fields(tmp_path):
    source = tmp_path / "events.jsonl"
    source.write_text(
        '\n{"category":" z ","count":2,"extra":false}\n'
        '{"category":"中文","count":4}\n \t\n'
        '{"category":"z","count":0}\n{"category":"A","count":3}\n'
        '{"category":"中文","count":5}',
        encoding="utf-8",
    )
    before = source.read_bytes()
    result = cli(source)
    success(result, {"A": 3, "z": 2, "中文": 9}, 5, 14)
    assert "中文" in result.stdout and source.read_bytes() == before


def test_empty_and_whitespace_only_input(tmp_path):
    source = tmp_path / "empty.jsonl"
    for text in ("", "\n\r\n\t \n"):
        source.write_text(text)
        success(cli(source), {}, 0, 0)


def test_exact_large_integer_counts(tmp_path):
    source = tmp_path / "large.jsonl"
    large = 10**80
    source.write_text(
        "\n".join(
            json.dumps({"category": "large", "count": count}) for count in (large, large + 1)
        ),
        encoding="utf-8",
    )
    success(cli(source), {"large": 2 * large + 1}, 2, 2 * large + 1)


def test_cli_and_file_errors(tmp_path):
    failure(cli())
    failure(cli(tmp_path, tmp_path))
    failure(cli("--unknown-option"))
    failure(cli(tmp_path / "missing"))
    failure(cli(tmp_path))


def test_invalid_record_types_have_no_partial_output(tmp_path):
    source = tmp_path / "bad.jsonl"
    invalid = [
        [],
        1,
        None,
        {},
        {"category": "x"},
        {"category": "x", "count": True},
        {"category": "x", "count": False},
        {"category": "x", "count": -1},
        {"category": "x", "count": 1.0},
        {"category": "x", "count": "1"},
        {"category": True, "count": 1},
        {"category": " \t", "count": 1},
    ]
    for value in invalid:
        source.write_text('{"category":"valid","count":3}\n' + json.dumps(value), encoding="utf-8")
        before = source.read_bytes()
        failure(cli(source))
        assert source.read_bytes() == before


def test_malformed_json_or_utf8_has_no_partial_output(tmp_path):
    source = tmp_path / "bad.jsonl"
    for data in (b'{"category":"valid","count":3}\n{bad', b"\xff\xfe"):
        source.write_bytes(data)
        failure(cli(source))
        assert source.read_bytes() == data
