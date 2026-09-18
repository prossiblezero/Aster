import json
from pathlib import Path
import subprocess
import sys

import pytest


SCRIPT = Path(__file__).with_name("linecounts.py")


def run_tool(*args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *map(str, args)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def test_counts_stripped_nonempty_lines_and_sorts_keys(tmp_path):
    source = tmp_path / "input.txt"
    source.write_text(
        "  zebra  \nApple\napple\n\n  two  words  \nApple\n\t\n",
        encoding="utf-8",
    )

    result = run_tool(source)

    assert result.returncode == 0
    assert result.stdout == (
        '{"Apple": 2, "apple": 1, "two  words": 1, "zebra": 1}\n'
    )
    assert result.stderr == ""


@pytest.mark.parametrize("contents", [b"", b" \n\t\r\n  "])
def test_empty_or_whitespace_only_file(tmp_path, contents):
    source = tmp_path / "empty.txt"
    source.write_bytes(contents)

    result = run_tool(source)

    assert result.returncode == 0
    assert result.stdout == "{}\n"
    assert result.stderr == ""


def test_unicode_crlf_no_final_newline_and_spaced_filename(tmp_path):
    source = tmp_path / "含 空格.txt"
    original = "  中文  \r\ne\u0301\r\né\r\n中文".encode("utf-8")
    source.write_bytes(original)

    result = run_tool(source)

    assert result.returncode == 0
    assert json.loads(result.stdout) == {"e\u0301": 1, "é": 1, "中文": 2}
    assert "中文" in result.stdout
    assert "\\u" not in result.stdout
    assert result.stderr == ""
    assert source.read_bytes() == original


@pytest.mark.parametrize("args", [(), ("one", "two"), ("--unknown",)])
def test_invalid_arguments_exit_two_without_stdout_or_traceback(args):
    result = run_tool(*args)

    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("kind", ["missing", "directory", "invalid_utf8"])
def test_unreadable_inputs_exit_two_without_stdout_or_traceback(tmp_path, kind):
    if kind == "missing":
        source = tmp_path / "missing.txt"
    elif kind == "directory":
        source = tmp_path
    else:
        source = tmp_path / "invalid.txt"
        source.write_bytes(b"valid\n\xff\n")

    result = run_tool(source)

    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr.startswith("error:")
    assert "Traceback" not in result.stderr


def test_help_exits_zero():
    result = run_tool("--help")

    assert result.returncode == 0
    assert "usage:" in result.stdout
    assert result.stderr == ""
