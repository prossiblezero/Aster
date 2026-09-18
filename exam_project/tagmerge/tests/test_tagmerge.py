import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tagmerge.py"
FIXTURES = ROOT / "fixtures"


def run_cli(*args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *map(str, args)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        check=False,
    )


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def assert_error(result):
    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr.strip()
    assert "Traceback" not in result.stderr


def assert_success(result, expected):
    assert result.returncode == 0
    assert result.stderr == ""
    assert result.stdout == json.dumps(expected, ensure_ascii=False) + "\n"


def test_original_fixtures():
    before = {p.name: p.read_bytes() for p in FIXTURES.iterdir()}
    assert len(before) == 6
    assert_success(run_cli(FIXTURES), {
        "birch": ["white"],
        "cedar": ["coastal", "evergreen", "tall"],
        "elm": [],
        "maple": ["gold", "red"],
        "oak": ["green", "large", "round", "small"],
        "\u94f6\u674f": ["\u79cb\u5929", "\u9ec4\u8272"],
    })
    assert {p.name: p.read_bytes() for p in FIXTURES.iterdir()} == before


def test_trimming_deduplication_and_exact_unicode(tmp_path):
    write_json(tmp_path / "b.json", [
        {"id": " z ", "tags": [" b ", "a", "b"], "extra": 42},
        {"id": "A", "tags": []},
        {"id": "\u00e9", "tags": ["\u00e9", "e\u0301"]},
    ])
    write_json(tmp_path / "a.json", [
        {"id": "z", "tags": ["a", " B "]},
        {"id": "a", "tags": ["x"]},
        {"id": "e\u0301", "tags": []},
    ])
    assert_success(run_cli(tmp_path), {
        "A": [], "a": ["x"], "e\u0301": [],
        "z": ["B", "a", "b"], "\u00e9": ["e\u0301", "\u00e9"],
    })


@pytest.mark.parametrize("empty_files", [False, True])
def test_empty_inputs(tmp_path, empty_files):
    if empty_files:
        write_json(tmp_path / "a.json", [])
        write_json(tmp_path / "b.json", [])
    assert_success(run_cli(tmp_path), {})


def test_only_direct_lowercase_json_files(tmp_path):
    write_json(tmp_path / "valid.json", [{"id": "ok", "tags": []}])
    for name in ["bad.txt", "bad.JSON", "bad.json.bak", "extensionless"]:
        (tmp_path / name).write_text("invalid", encoding="utf-8")
    nested = tmp_path / "nested.json"
    nested.mkdir()
    (nested / "bad.json").write_text("invalid", encoding="utf-8")
    assert_success(run_cli(tmp_path), {"ok": []})


@pytest.mark.parametrize("args", [[], ["a", "b"], ["--unknown"], ["--unknown", "a"]])
def test_bad_arguments(args):
    assert_error(run_cli(*args))


def test_help():
    result = run_cli("--help")
    assert result.returncode == 0
    assert "usage:" in result.stdout
    assert result.stderr == ""


def test_missing_directory(tmp_path):
    assert_error(run_cli(tmp_path / "missing"))


def test_not_a_directory(tmp_path):
    path = tmp_path / "file.json"
    write_json(path, [])
    assert_error(run_cli(path))


@pytest.mark.parametrize("data", [
    {}, None, "text", 42,
    [None], [[]], ["record"], [42],
    [{}], [{"id": "x"}], [{"tags": []}],
    [{"id": None, "tags": []}], [{"id": 1, "tags": []}],
    [{"id": True, "tags": []}], [{"id": [], "tags": []}],
    [{"id": "", "tags": []}], [{"id": " \t\n", "tags": []}],
    [{"id": "\u3000", "tags": []}],
    [{"id": "x", "tags": None}], [{"id": "x", "tags": "tag"}],
    [{"id": "x", "tags": {}}], [{"id": "x", "tags": 1}],
    [{"id": "x", "tags": [None]}], [{"id": "x", "tags": [1]}],
    [{"id": "x", "tags": [True]}], [{"id": "x", "tags": [[]]}],
    [{"id": "x", "tags": [{}]}], [{"id": "x", "tags": [""]}],
    [{"id": "x", "tags": [" \t\n"]}],
    [{"id": "x", "tags": ["\u3000"]}],
])
def test_invalid_structure(tmp_path, data):
    write_json(tmp_path / "bad.json", data)
    assert_error(run_cli(tmp_path))


@pytest.mark.parametrize("raw", [b"\xff", b"[", b"", b"[] trailing", b'[{"id":"x","tags":[],}]'])
def test_invalid_encoding_or_json(tmp_path, raw):
    (tmp_path / "bad.json").write_bytes(raw)
    assert_error(run_cli(tmp_path))


def test_no_partial_stdout_after_valid_file(tmp_path):
    write_json(tmp_path / "a.json", [{"id": "valid", "tags": ["tag"]}])
    (tmp_path / "z.json").write_text("invalid", encoding="utf-8")
    assert_error(run_cli(tmp_path))
