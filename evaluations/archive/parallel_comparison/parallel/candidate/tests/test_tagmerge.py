import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tagmerge.py"


def run_cli(*args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *map(str, args)],
        capture_output=True,
        encoding="utf-8",
        check=False,
    )


def write_records(directory, records, name="data.json"):
    (directory / name).write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")


def assert_error(result):
    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr.strip()
    assert "Traceback" not in result.stderr


def test_original_fixtures():
    fixtures = ROOT / "fixtures"
    before = {path.name: path.read_bytes() for path in fixtures.iterdir()}
    result = run_cli(fixtures)
    assert result.returncode == 0
    assert result.stderr == ""
    expected = {
        "birch": ["white"],
        "cedar": ["coastal", "evergreen", "tall"],
        "elm": [],
        "maple": ["gold", "red"],
        "oak": ["green", "large", "round", "small"],
        "银杏": ["秋天", "黄色"],
    }
    assert result.stdout == json.dumps(expected, ensure_ascii=False) + "\n"
    assert before == {path.name: path.read_bytes() for path in fixtures.iterdir()}
    assert len(before) == 6


@pytest.mark.parametrize("empty_files", [False, True])
def test_empty_directory_or_arrays(tmp_path, empty_files):
    if empty_files:
        write_records(tmp_path, [], "a.json")
        write_records(tmp_path, [], "b.json")
    result = run_cli(tmp_path)
    assert result.returncode == 0
    assert result.stdout == "{}\n"
    assert result.stderr == ""


def test_only_direct_lowercase_json_files(tmp_path):
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "bad.json").write_text("invalid", encoding="utf-8")
    (tmp_path / "directory.json").mkdir()
    for name in ["bad.txt", "bad.JSON", "bad.json.bak"]:
        (tmp_path / name).write_text("invalid", encoding="utf-8")
    write_records(tmp_path, [{"id": "ok", "tags": []}])
    result = run_cli(tmp_path)
    assert result.returncode == 0
    assert result.stdout == '{"ok": []}\n'
    assert result.stderr == ""


def test_trim_sort_deduplicate_without_case_or_unicode_normalization(tmp_path):
    write_records(tmp_path, [
        {"id": " z ", "tags": [" b ", "A", "b", "a"], "extra": 42},
        {"id": "z", "tags": ["a", "c"]},
        {"id": "Z", "tags": []},
        {"id": "é", "tags": ["é", "e\u0301"]},
        {"id": "e\u0301", "tags": []},
    ])
    result = run_cli(tmp_path)
    expected = {"Z": [], "e\u0301": [], "z": ["A", "a", "b", "c"], "é": ["e\u0301", "é"]}
    assert result.returncode == 0
    assert result.stdout == json.dumps(expected, ensure_ascii=False) + "\n"
    assert result.stderr == ""


@pytest.mark.parametrize("args", [[], ["one", "two"], ["--unknown"], ["--DATA_DIR=x"]])
def test_invalid_arguments(args):
    assert_error(run_cli(*args))


def test_help():
    result = run_cli("--help")
    assert result.returncode == 0
    assert "DATA_DIR" in result.stdout
    assert result.stderr == ""


def test_missing_directory(tmp_path):
    assert_error(run_cli(tmp_path / "missing"))


def test_file_instead_of_directory(tmp_path):
    write_records(tmp_path, [])
    assert_error(run_cli(tmp_path / "data.json"))


@pytest.mark.parametrize("records", [
    {}, None, 1, "array", True,
    [None], [[]], ["record"], [1], [True],
    [{}], [{"id": "x"}], [{"tags": []}],
    [{"id": None, "tags": []}], [{"id": 1, "tags": []}],
    [{"id": True, "tags": []}], [{"id": [], "tags": []}],
    [{"id": "", "tags": []}], [{"id": " \t\n", "tags": []}],
    [{"id": "x", "tags": None}], [{"id": "x", "tags": "tag"}],
    [{"id": "x", "tags": {}}], [{"id": "x", "tags": 1}],
    [{"id": "x", "tags": [None]}], [{"id": "x", "tags": [1]}],
    [{"id": "x", "tags": [True]}], [{"id": "x", "tags": [[]]}],
    [{"id": "x", "tags": [""]}], [{"id": "x", "tags": [" \t\n"]}],
])
def test_invalid_structure(tmp_path, records):
    write_records(tmp_path, records)
    assert_error(run_cli(tmp_path))


@pytest.mark.parametrize("content", [b"\xff", b"[", b"[] trailing", b"", b'[ {"id": "x", "tags": [], "extra": NaN} ]'])
def test_invalid_encoding_or_json(tmp_path, content):
    (tmp_path / "bad.json").write_bytes(content)
    assert_error(run_cli(tmp_path))


def test_no_partial_output_when_later_file_is_invalid(tmp_path):
    write_records(tmp_path, [{"id": "valid", "tags": ["tag"]}], "a.json")
    (tmp_path / "z.json").write_text("invalid", encoding="utf-8")
    assert_error(run_cli(tmp_path))
