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
    def run_cli(self, *arguments: object) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), *(str(argument) for argument in arguments)],
            text=True,
            capture_output=True,
            check=False,
        )

    def make_input(self, directory: Path, content: bytes) -> Path:
        path = directory / "events.jsonl"
        path.write_bytes(content)
        return path

    def assert_cli_error(self, result: subprocess.CompletedProcess[str]) -> None:
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertNotEqual(result.stderr.strip(), "")
        self.assertNotIn("Traceback", result.stderr)

    def test_aggregates_strips_sorts_and_preserves_unicode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            content = (
                '\n {"category":" zeta ","count":2,"ignored":true}\n'
                '{"category":"álpha","count":3}\n'
                '{"category":"zeta","count":4}\n'
                '{"category":"Beta","count":0}\n'
            ).encode("utf-8")
            path = self.make_input(directory, content)
            original = path.read_bytes()

            result = self.run_cli(path)

            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stderr, "")
            self.assertTrue(result.stdout.endswith("\n"))
            self.assertEqual(result.stdout.count("\n"), 1)
            self.assertIn("álpha", result.stdout)
            self.assertNotIn("\\u00e1", result.stdout)
            self.assertEqual(
                json.loads(result.stdout),
                {
                    "categories": [
                        {"category": "Beta", "count": 0},
                        {"category": "zeta", "count": 6},
                        {"category": "álpha", "count": 3},
                    ],
                    "records": 4,
                    "total": 9,
                },
            )
            self.assertEqual(path.read_bytes(), original)

    def test_empty_and_whitespace_only_input(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = self.make_input(Path(temporary_directory), b"\n  \t\r\n")
            result = self.run_cli(path)

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            json.loads(result.stdout),
            {"categories": [], "records": 0, "total": 0},
        )

    def test_counts_larger_than_64_bits_remain_exact(self) -> None:
        huge = 2**100
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = self.make_input(
                Path(temporary_directory),
                f'{{"category":"large","count":{huge}}}\n'.encode(),
            )
            result = self.run_cli(path)

        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)["total"], huge)

    def test_wrong_argument_counts_and_unknown_option(self) -> None:
        for arguments in [(), ("one", "two"), ("--unknown",)]:
            with self.subTest(arguments=arguments):
                self.assert_cli_error(self.run_cli(*arguments))

    def test_missing_file_and_directory_are_errors(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            for path in [directory / "missing.jsonl", directory]:
                with self.subTest(path=path):
                    self.assert_cli_error(self.run_cli(path))

    def test_unreadable_file_is_reported(self) -> None:
        with (
            mock.patch.object(Path, "is_file", return_value=True),
            mock.patch.object(Path, "open", side_effect=PermissionError("denied")),
        ):
            with self.assertRaisesRegex(eventcount.InputError, "cannot read input file"):
                eventcount.aggregate(Path("events.jsonl"))

    def test_invalid_utf8_and_json_are_errors_without_mutation(self) -> None:
        invalid_inputs = [
            b'{"category":"x","count":1}\n\xff',
            b'{"category":"x","count":1\n',
            b'{"category":"x","count":NaN}\n',
        ]
        for content in invalid_inputs:
            with self.subTest(content=content), tempfile.TemporaryDirectory() as temp:
                path = self.make_input(Path(temp), content)
                self.assert_cli_error(self.run_cli(path))
                self.assertEqual(path.read_bytes(), content)

    def test_invalid_record_shapes_and_fields(self) -> None:
        invalid_records = [
            [],
            {"count": 1},
            {"category": "x"},
            {"category": "", "count": 1},
            {"category": "   ", "count": 1},
            {"category": 4, "count": 1},
            {"category": "x", "count": True},
            {"category": "x", "count": 1.0},
            {"category": "x", "count": -1},
            {"category": "x", "count": "1"},
        ]
        for record in invalid_records:
            with self.subTest(record=record), tempfile.TemporaryDirectory() as temp:
                path = self.make_input(
                    Path(temp), (json.dumps(record) + "\n").encode("utf-8")
                )
                self.assert_cli_error(self.run_cli(path))

    def test_late_invalid_record_produces_no_partial_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = self.make_input(
                Path(temporary_directory),
                b'{"category":"valid","count":1}\n{"category":"bad"}\n',
            )
            self.assert_cli_error(self.run_cli(path))


if __name__ == "__main__":
    unittest.main()
