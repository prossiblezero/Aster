from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "uniqwords.py"


def run_tool(*args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )


def assert_error(result: subprocess.CompletedProcess[bytes]) -> None:
    assert result.returncode == 2
    assert result.stdout == b""
    assert result.stderr
    assert b"Traceback" not in result.stderr


def test_counts_sorts_and_preserves_words(tmp_path: Path) -> None:
    source = tmp_path / "input with spaces.txt"
    source.write_text("banana Apple banana apple Apple, 世界 世界", encoding="utf-8")

    result = run_tool(str(source))

    assert result.returncode == 0
    assert result.stderr == b""
    assert result.stdout.decode("utf-8") == (
        '{"Apple": 1, "Apple,": 1, "apple": 1, "banana": 2, "世界": 2}\n'
    )
    assert b"\\u" not in result.stdout


@pytest.mark.parametrize("content", ["", " \t\r\n\u3000\u00a0"])
def test_empty_or_whitespace_only_file(tmp_path: Path, content: str) -> None:
    source = tmp_path / "empty.txt"
    source.write_bytes(content.encode("utf-8"))

    result = run_tool(str(source))

    assert result.returncode == 0
    assert result.stdout == b"{}\n"
    assert result.stderr == b""


def test_unicode_whitespace_crlf_and_no_final_newline(tmp_path: Path) -> None:
    source = tmp_path / "line-endings.txt"
    source.write_bytes("甲\u2003乙\r\n甲\u2028丙".encode("utf-8"))
    before = source.read_bytes()

    result = run_tool(str(source))

    assert result.returncode == 0
    assert json.loads(result.stdout) == {"丙": 1, "乙": 1, "甲": 2}
    assert result.stderr == b""
    assert source.read_bytes() == before


@pytest.mark.parametrize("args", [(), ("one.txt", "two.txt"), ("--unknown",)])
def test_argument_errors(args: tuple[str, ...]) -> None:
    assert_error(run_tool(*args))


def test_missing_file(tmp_path: Path) -> None:
    assert_error(run_tool(str(tmp_path / "missing.txt")))


def test_directory_path(tmp_path: Path) -> None:
    assert_error(run_tool(str(tmp_path)))


def test_invalid_utf8(tmp_path: Path) -> None:
    source = tmp_path / "invalid.txt"
    source.write_bytes(b"valid\xffinvalid")

    assert_error(run_tool(str(source)))


def test_help() -> None:
    result = run_tool("--help")

    assert result.returncode == 0
    assert result.stdout
    assert b"usage:" in result.stdout
    assert result.stderr == b""
