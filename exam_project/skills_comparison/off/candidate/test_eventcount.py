from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import eventcount


SCRIPT = Path(__file__).with_name("eventcount.py")


class EventCountCLITests(unittest.TestCase):
    def run_cli(self, *arguments: str) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), *arguments],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def make_input(self, contents: bytes) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        temporary_directory = tempfile.TemporaryDirectory()
        path = Path(temporary_directory.name) / "events.jsonl"
        path.write_bytes(contents)
        return temporary_directory, path

    def assert_cli_error(self, result: subprocess.CompletedProcess[bytes]) -> None:
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, b"")
        self.assertTrue(result.stderr.strip())
        self.assertNotIn(b"Traceback", result.stderr)

    def test_aggregates_sorts_strips_and_preserves_unicode(self) -> None:
        large_count = 2**70
        contents = (
            '\n  \n'
            '{"category":" zeta ","count":2,"ignored":true}\n'
            '{"category":"éclair","count":3}\n'
            f'{{"category":"zeta","count":{large_count}}}\n'
            '{"category":"Alpha","count":0}\n'
        ).encode("utf-8")
        temporary_directory, path = self.make_input(contents)
        self.addCleanup(temporary_directory.cleanup)

        result = self.run_cli(str(path))

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, b"")
        self.assertTrue(result.stdout.endswith(b"\n"))
        self.assertEqual(result.stdout.count(b"\n"), 1)
        self.assertIn("éclair".encode(), result.stdout)
        self.assertNotIn(b"\\u00e9", result.stdout)
        self.assertEqual(
            json.loads(result.stdout),
            {
                "categories": [
                    {"category": "Alpha", "count": 0},
                    {"category": "zeta", "count": large_count + 2},
                    {"category": "éclair", "count": 3},
                ],
                "records": 4,
                "total": large_count + 5,
            },
        )

    def test_empty_and_blank_input(self) -> None:
        temporary_directory, path = self.make_input("\n \t\r\n".encode())
        self.addCleanup(temporary_directory.cleanup)

        result = self.run_cli(str(path))

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, b"")
        self.assertEqual(
            json.loads(result.stdout),
            {"categories": [], "records": 0, "total": 0},
        )

    def test_invalid_record_variants(self) -> None:
        invalid_lines = [
            b"[]\n",
            b'{"count":1}\n',
            b'{"category":"x"}\n',
            b'{"category":"   ","count":1}\n',
            b'{"category":2,"count":1}\n',
            b'{"category":"x","count":true}\n',
            b'{"category":"x","count":-1}\n',
            b'{"category":"x","count":1.0}\n',
        ]
        for contents in invalid_lines:
            with self.subTest(contents=contents):
                temporary_directory, path = self.make_input(contents)
                try:
                    self.assert_cli_error(self.run_cli(str(path)))
                finally:
                    temporary_directory.cleanup()

    def test_invalid_json_and_nonstandard_constants(self) -> None:
        for contents in (b"{bad json}\n", b'{"category":"x","count":NaN}\n'):
            with self.subTest(contents=contents):
                temporary_directory, path = self.make_input(contents)
                try:
                    result = self.run_cli(str(path))
                    self.assert_cli_error(result)
                    self.assertIn(b"line 1", result.stderr)
                finally:
                    temporary_directory.cleanup()

    def test_invalid_utf8_reports_line_and_emits_no_partial_output(self) -> None:
        temporary_directory, path = self.make_input(
            b'{"category":"valid","count":1}\n\xff\n'
        )
        self.addCleanup(temporary_directory.cleanup)

        result = self.run_cli(str(path))

        self.assert_cli_error(result)
        self.assertIn(b"line 2", result.stderr)

    def test_argument_errors(self) -> None:
        for arguments in ((), ("--unknown",), ("one", "two")):
            with self.subTest(arguments=arguments):
                self.assert_cli_error(self.run_cli(*arguments))

    def test_missing_and_non_file_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            missing = str(Path(directory) / "missing.jsonl")
            for path in (missing, directory):
                with self.subTest(path=path):
                    self.assert_cli_error(self.run_cli(path))

    def test_read_error_is_converted_to_input_error(self) -> None:
        temporary_directory, path = self.make_input(b"")
        self.addCleanup(temporary_directory.cleanup)
        with mock.patch("builtins.open", side_effect=PermissionError("denied")):
            with self.assertRaisesRegex(eventcount.InputError, "cannot read"):
                eventcount.summarize(str(path))


if __name__ == "__main__":
    unittest.main()
