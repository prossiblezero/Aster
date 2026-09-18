from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().with_name("notetally.py")


class NoteTallyCliTests(unittest.TestCase):
    def run_cli(self, *arguments: object, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), *(str(argument) for argument in arguments)],
            cwd=cwd,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )

    def assert_input_error(self, result: subprocess.CompletedProcess[str]) -> None:
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertTrue(result.stderr.strip())
        self.assertNotIn("Traceback", result.stderr)

    def test_recursive_parsing_sorting_and_ignored_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            notes = Path(temporary_directory) / "notes"
            (notes / "nested").mkdir(parents=True)
            (notes / "main.note").write_text(
                "  todo  : first: keep colon\n"
                "# comment: ignored\n"
                "   # indented comment\n"
                "\n"
                "Todo: preserve case\n"
                "two words: preserve internal spaces\n",
                encoding="utf-8",
            )
            (notes / "nested" / "中文.note").write_text(
                "标签: 内容\ntodo: second\n", encoding="utf-8"
            )
            (notes / "ignored.txt").write_text("bad input", encoding="utf-8")

            result = self.run_cli(notes)

            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stderr, "")
            self.assertEqual(
                json.loads(result.stdout),
                {"Todo": 1, "todo": 2, "two words": 1, "标签": 1},
            )
            self.assertEqual(
                result.stdout,
                '{"Todo":1,"todo":2,"two words":1,"标签":1}\n',
            )

    def test_default_directory_is_relative_to_current_working_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            cwd = Path(temporary_directory)
            (cwd / "inbox_notes").mkdir()
            (cwd / "inbox_notes" / "default.note").write_text(
                "default: found\n", encoding="utf-8"
            )

            result = self.run_cli(cwd=cwd)

            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout, '{"default":1}\n')
            self.assertEqual(result.stderr, "")

    def test_total_uses_default_directory_relative_to_current_working_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            cwd = Path(temporary_directory)
            (cwd / "inbox_notes").mkdir()
            (cwd / "inbox_notes" / "default.note").write_text(
                "todo: first\ntodo: second\ndone: third\n", encoding="utf-8"
            )

            result = self.run_cli("--total", cwd=cwd)

            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "3\n")
            self.assertEqual(result.stderr, "")

    def test_total_accepts_explicit_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            notes = Path(temporary_directory)
            (notes / "items.note").write_text(
                "one: first\ntwo: second\n", encoding="utf-8"
            )

            result = self.run_cli(notes, "--total")

            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "2\n")
            self.assertEqual(result.stderr, "")

    def test_empty_and_comment_only_input_outputs_empty_object(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            notes = Path(temporary_directory)
            (notes / "empty.note").write_text(
                "\n  \t\n # comment\n", encoding="utf-8"
            )

            result = self.run_cli(notes)

            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "{}\n")
            self.assertEqual(result.stderr, "")

            total_result = self.run_cli("--total", notes)

            self.assertEqual(total_result.returncode, 0)
            self.assertEqual(total_result.stdout, "0\n")
            self.assertEqual(total_result.stderr, "")

    def test_each_bad_line_format_is_an_input_error(self) -> None:
        bad_lines = ["missing colon", ": content", "label:", "  :  "]
        for bad_line in bad_lines:
            with self.subTest(line=bad_line), tempfile.TemporaryDirectory() as temporary_directory:
                notes = Path(temporary_directory)
                (notes / "bad.note").write_text(bad_line + "\n", encoding="utf-8")

                self.assert_input_error(self.run_cli(notes))

    def test_invalid_utf8_is_an_input_error_without_partial_json(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            notes = Path(temporary_directory)
            (notes / "a-valid.note").write_text("ok: value\n", encoding="utf-8")
            (notes / "z-invalid.note").write_bytes(b"bad: \xff\n")

            self.assert_input_error(self.run_cli(notes))

    def test_missing_directory_and_file_path_are_input_errors(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            file_path = root / "input.note"
            file_path.write_text("tag: value\n", encoding="utf-8")
            for path in (root / "missing", file_path):
                with self.subTest(path=path):
                    self.assert_input_error(self.run_cli(path))

    def test_unknown_and_extra_arguments_are_input_errors(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            notes = Path(temporary_directory)
            for arguments in (("--unknown",), (notes, notes)):
                with self.subTest(arguments=arguments):
                    self.assert_input_error(self.run_cli(*arguments))


if __name__ == "__main__":
    unittest.main()
