"""Operator-owned independent checks, not visible during candidate generation."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path


def cli(*arguments):
    return subprocess.run(
        [sys.executable, "tagmerge.py", *map(str, arguments)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=5,
    )


def success(result, expected):
    assert result.returncode == 0 and result.stderr == ""
    assert result.stdout.endswith("\n")
    actual = json.loads(result.stdout)
    assert actual == expected
    assert list(actual) == sorted(actual)
    assert all(tags == sorted(set(tags)) for tags in actual.values())


def failure(result):
    assert result.returncode == 2 and result.stdout == ""
    assert result.stderr.strip() and "Traceback" not in result.stderr


def put(directory, name, value):
    (directory / name).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def test_required_artifacts():
    assert Path("tagmerge.py").is_file()
    assert Path("README.md").is_file() and Path("README.md").read_text().strip()


def test_all_six_supplied_inputs_are_merged_and_preserved():
    before = {p.name: p.read_bytes() for p in Path("fixtures").glob("*.json")}
    assert len(before) == 6
    success(
        cli("fixtures"),
        {
            "birch": ["white"],
            "cedar": ["coastal", "evergreen", "tall"],
            "elm": [],
            "maple": ["gold", "red"],
            "oak": ["green", "large", "round", "small"],
            "银杏": ["秋天", "黄色"],
        },
    )
    assert {p.name: p.read_bytes() for p in Path("fixtures").glob("*.json")} == before
    expected = FIXTURE_HASHES
    assert {name: hashlib.sha256(data).hexdigest() for name, data in before.items()} == expected


def test_generic_merge_unicode_and_whitespace(tmp_path):
    put(
        tmp_path,
        "one.json",
        [{"id": " B ", "tags": [" z ", "a", "a"]}, {"id": "中文", "tags": ["早"]}],
    )
    put(
        tmp_path,
        "two.json",
        [
            {"id": "B", "tags": ["b"], "extra": "ignored"},
            {"id": "中文", "tags": ["晚", "早"]},
            {"id": "a", "tags": []},
        ],
    )
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    result = cli(tmp_path)
    success(result, {"B": ["a", "b", "z"], "a": [], "中文": ["早", "晚"]})
    assert "中文" in result.stdout
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before


def test_empty_directory_and_nonrecursive_selection(tmp_path):
    success(cli(tmp_path), {})
    put(tmp_path, "empty.json", [])
    (tmp_path / "notes.txt").write_text("not JSON")
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "bad.json").write_text("not JSON")
    success(cli(tmp_path), {})


def test_parameters_and_directory_errors(tmp_path):
    failure(cli())
    failure(cli(tmp_path, tmp_path))
    failure(cli("--unknown-option"))
    failure(cli(tmp_path / "missing"))
    (tmp_path / "file.txt").write_text("file")
    failure(cli(tmp_path / "file.txt"))


def test_invalid_structure_and_field_types(tmp_path):
    for value in (
        {},
        [1],
        [{"id": "ok"}],
        [{"id": True, "tags": []}],
        [{"id": " ", "tags": []}],
        [{"id": "ok", "tags": "tag"}],
        [{"id": "ok", "tags": [1]}],
        [{"id": "ok", "tags": [" "]}],
    ):
        put(tmp_path, "bad.json", value)
        failure(cli(tmp_path))


def test_invalid_json_and_utf8(tmp_path):
    for data in (b"[invalid", b"\xff\xfe"):
        source = tmp_path / "bad.json"
        source.write_bytes(data)
        failure(cli(tmp_path))
        assert source.read_bytes() == data


FIXTURE_HASHES = {
    "central.json": "a3669fa4d19ae76c0aa41c077d92336e6f9765de9e90a9d8f68a42bb14a524a6",
    "east.json": "d4a07f392e94b3fac7ab663bc01315156ed98a373a03f26ab31fa8721f07c660",
    "island.json": "8b62456959c0a97084541485810caaa8619d54218ddcc5786684b137bf6ec6b6",
    "north.json": "85537622a9c0ac868c89b6f1756f7adc7e487798f01a672773e373c6431786c8",
    "south.json": "1c75350d46fda26a11dbdeae90012394cc7061c48fed65674681980d7d9ff007",
    "west.json": "8f5aa6826a7304b648cb8525ecfdb904377a17f063a34866820e9296c53e729b",
}
