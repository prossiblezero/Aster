#!/usr/bin/env python3
"""End-to-end self-tests for notetally.py."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().with_name("notetally.py")


class NoteTallyTests(unittest.TestCase):
    def run_cli(
        self, *arguments: str, cwd: Path | None = None
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), *arguments],
            cwd=cwd,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )

    def test_explicit_directory_counts_valid_notes_recursively(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "nested").mkdir()
            (root / "first.note").write_text(
                "  todo : first item  \n"
                "# comment\n"
                "   # indented comment\n"
                "\n"
                "Todo: preserve case\n"
                "topic  name: preserve: later colons\n",
                encoding="utf-8",
            )
            (root / "nested" / "second.note").write_text(
                "记事: 中文内容\ntodo: second item\n", encoding="utf-8"
            )
            (root / "ignored.txt").write_text("todo: ignored\n", encoding="utf-8")
            (root / "ignored.NOTE").write_text("todo: ignored\n", encoding="utf-8")
            original = (root / "first.note").read_bytes()

            result = self.run_cli(str(root))
            total_result = self.run_cli(str(root), "--total")

            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stderr, "")
            self.assertEqual(
                json.loads(result.stdout),
                {"Todo": 1, "todo": 2, "topic  name": 1, "记事": 1},
            )
            self.assertEqual(
                result.stdout,
                '{"Todo": 1, "todo": 2, "topic  name": 1, "记事": 1}\n',
            )
            self.assertEqual(total_result.returncode, 0)
            self.assertEqual(total_result.stdout, "5\n")
            self.assertEqual(total_result.stderr, "")
            self.assertEqual((root / "first.note").read_bytes(), original)

    def test_default_directory_is_relative_to_working_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            notes = root / "inbox_notes"
            notes.mkdir()
            (notes / "default.note").write_text("default: found\n", encoding="utf-8")

            result = self.run_cli(cwd=root)

            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout, '{"default": 1}\n')
            self.assertEqual(result.stderr, "")

    def test_total_uses_default_directory_relative_to_working_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            notes = root / "inbox_notes"
            notes.mkdir()
            (notes / "default.note").write_text(
                "todo: first\ndone: second\ntodo: third\n", encoding="utf-8"
            )

            result = self.run_cli("--total", cwd=root)

            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "3\n")
            self.assertEqual(result.stderr, "")

    def test_empty_and_comment_only_input_outputs_empty_object(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "empty.note").write_text(
                " \n\t\n # comment\n", encoding="utf-8"
            )

            result = self.run_cli(str(root))
            total_result = self.run_cli("--total", str(root))

            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "{}\n")
            self.assertEqual(result.stderr, "")
            self.assertEqual(total_result.returncode, 0)
            self.assertEqual(total_result.stdout, "0\n")
            self.assertEqual(total_result.stderr, "")

    def test_bad_note_formats_exit_two_without_json(self) -> None:
        bad_lines = ["missing colon", ": content", "label:", "  :  "]
        for bad_line in bad_lines:
            with self.subTest(line=bad_line), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "bad.note").write_text(bad_line + "\n", encoding="utf-8")

                result = self.run_cli(str(root))

                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertIn("error:", result.stderr)
                self.assertNotIn("Traceback", result.stderr)

    def test_invalid_utf8_exits_two_without_json(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "bad.note").write_bytes(b"valid: note\n\xff")

            result = self.run_cli(str(root))

            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, "")
            self.assertIn("invalid UTF-8", result.stderr)
            self.assertNotIn("Traceback", result.stderr)

    def test_invalid_directory_arguments_exit_two(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            file_path = root / "a-file"
            file_path.write_text("", encoding="utf-8")
            cases = [str(root / "missing"), str(file_path)]
            for argument in cases:
                with self.subTest(argument=argument):
                    result = self.run_cli(argument)
                    self.assertEqual(result.returncode, 2)
                    self.assertEqual(result.stdout, "")
                    self.assertIn("error:", result.stderr)
                    self.assertNotIn("Traceback", result.stderr)

    def test_unknown_and_extra_arguments_exit_two(self) -> None:
        for arguments in [("--unknown",), ("one", "two")]:
            with self.subTest(arguments=arguments):
                result = self.run_cli(*arguments)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertIn("error:", result.stderr)
                self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
