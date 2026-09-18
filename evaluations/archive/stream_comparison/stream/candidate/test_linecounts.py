import json
import subprocess
import sys
from pathlib import Path


SCRIPT = Path(__file__).with_name("linecounts.py")


def run_linecounts(*args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *map(str, args)],
        capture_output=True,
        check=False,
    )


def test_counts_stripped_nonblank_lines_and_sorts_keys(tmp_path):
    source = tmp_path / "input file.txt"
    original = "  beta  \r\nalpha\r\n\t\r\nalpha\r\nBeta\r\n中 文\r\n中  文"
    source.write_bytes(original.encode("utf-8"))

    result = run_linecounts(source)

    assert result.returncode == 0
    assert result.stderr == b""
    assert result.stdout.decode("utf-8") == (
        '{"Beta": 1, "alpha": 2, "beta": 1, "中  文": 1, "中 文": 1}\n'
    )
    assert source.read_bytes() == original.encode("utf-8")


def test_preserves_case_internal_whitespace_and_unicode_codepoints(tmp_path):
    source = tmp_path / "unicode.txt"
    source.write_text("Word\nword\na b\na  b\né\ne\u0301", encoding="utf-8")

    result = run_linecounts(source)
    output = json.loads(result.stdout.decode("utf-8"))

    assert result.returncode == 0
    assert result.stderr == b""
    assert output == {"Word": 1, "word": 1, "a b": 1, "a  b": 1, "é": 1, "e\u0301": 1}
    assert b"\\u" not in result.stdout


def test_empty_and_whitespace_only_files(tmp_path):
    for name, content in (("empty.txt", b""), ("blank.txt", " \n\t\r\n".encode())):
        source = tmp_path / name
        source.write_bytes(content)

        result = run_linecounts(source)

        assert result.returncode == 0
        assert result.stdout == b"{}\n"
        assert result.stderr == b""


def test_missing_extra_and_unknown_arguments_exit_two():
    for args in ((), ("one", "two"), ("--unknown",)):
        result = run_linecounts(*args)

        assert result.returncode == 2
        assert result.stdout == b""
        assert result.stderr
        assert b"Traceback" not in result.stderr


def test_missing_directory_and_invalid_utf8_inputs_exit_two(tmp_path):
    invalid = tmp_path / "invalid.txt"
    invalid.write_bytes(b"valid\n\xff\n")

    for source in (tmp_path / "missing.txt", tmp_path, invalid):
        result = run_linecounts(source)

        assert result.returncode == 2
        assert result.stdout == b""
        assert result.stderr.startswith(b"error:")
        assert b"Traceback" not in result.stderr


def test_help_exits_zero():
    result = run_linecounts("--help")

    assert result.returncode == 0
    assert result.stdout
    assert result.stderr == b""
