"""Independent operator acceptance, copied only into an isolated grading workspace."""

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path("notetally.py").resolve()


def cli(cwd, *arguments):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *map(str, arguments)],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=5,
    )


def seed(root, files):
    root.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data if isinstance(data, bytes) else data.encode())
    return root


def success(result, expected):
    assert result.returncode == 0 and result.stderr == "" and result.stdout.endswith("\n")
    actual = json.loads(result.stdout)
    assert actual == expected
    if isinstance(expected, dict):
        assert list(actual) == sorted(expected) and all(
            type(value) is int for value in actual.values()
        )
    else:
        assert type(actual) is int


def failure(result):
    assert result.returncode == 2 and result.stdout == ""
    assert result.stderr.strip() and "Traceback" not in result.stderr


def test_required_artifacts_and_documented_default():
    assert SCRIPT.is_file()
    assert "inbox_notes" in Path("README.md").read_text()
    assert any(
        path.name.startswith("test")
        for path in Path(".").rglob("*.py")
        if path != Path("__evaluation_tests.py")
    )


def test_default_directory_is_not_cwd_or_notes(tmp_path):
    seed(tmp_path / "inbox_notes", {"a.note": "事项: one\n事项: two\n"})
    seed(tmp_path / "notes", {"wrong.note": "wrong: ignored\n"})
    seed(tmp_path, {"wrong.note": "wrong: ignored\n"})
    result = cli(tmp_path)
    success(result, {"事项": 2})
    assert "事项" in result.stdout


def test_explicit_directory_recursion_sorting_and_preservation(tmp_path):
    root = seed(
        tmp_path / "explicit data",
        {
            "one.note": "# comment\nZ: one\na: two: with colon\n Z : three\n",
            "nested/二.note": "\u3000事项\u3000:\u3000text\nAlpha: last\n",
            "ignored.txt": b"\xffinvalid ignored file\n",
        },
    )
    before = {
        str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()
    }
    success(cli(tmp_path, root), {"Alpha": 1, "Z": 2, "a": 1, "事项": 1})
    assert before == {
        str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()
    }


def test_empty_comments_and_crlf(tmp_path):
    root = seed(tmp_path / "empty", {})
    success(cli(tmp_path, root), {})
    seed(root, {"blank.note": "\r\n \t\n # comment\n\u3000\n"})
    success(cli(tmp_path, root), {})
    seed(root, {"lines.note": b"b: first\r\na: second\r\nb: final"})
    success(cli(tmp_path, root), {"a": 1, "b": 2})


def test_invalid_rows_do_not_emit_partial_json(tmp_path):
    root = seed(tmp_path / "bad", {})
    for invalid in ("no colon", ": missing tag", "tag:   "):
        seed(root, {"input.note": "valid: first\n" + invalid + "\n"})
        failure(cli(tmp_path, root))


def test_missing_non_directory_and_encoding_errors(tmp_path):
    failure(cli(tmp_path, tmp_path / "missing"))
    root = seed(tmp_path / "bad", {"input.note": b"valid: first\n\xff\xfe\n"})
    failure(cli(tmp_path, root))
    failure(cli(tmp_path, root / "input.note"))


def test_invalid_arguments(tmp_path):
    failure(cli(tmp_path, "one", "two"))
    failure(cli(tmp_path, "--not-an-option"))
