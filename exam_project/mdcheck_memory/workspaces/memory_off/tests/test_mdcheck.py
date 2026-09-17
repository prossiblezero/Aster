"""End-to-end and API checks for the mdcheck CLI."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import mdcheck


class MdcheckTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "docs"
        self.root.mkdir()

    def write(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def cli(self, *args):
        return subprocess.run(
            [sys.executable, "-m", "mdcheck", *map(str, args)],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )

    def test_json_paths_images_fragments_and_duplicates(self):
        self.write("sub/guide one.md", "# Title\n")
        self.write("sub/assets/icon.png", "image")
        self.write(
            "sub/index.md",
            "[ok](guide%20one.md#missing) ![icon](assets/icon.png) "
            "[dir](../) [unicode](caf%C3%A9.md)\n"
            "[bad](gone.md) [bad again](gone.md) [fragment](#section)\n"
            "[web](https://example.org/no.md) [mail](mailto:x@y.z) "
            "[cdn](//host/no.md)\n",
        )
        self.write("sub/café.md", "# Café\n")
        result = self.cli(self.root, "--format", "json")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            json.loads(result.stdout),
            {
                "checked": 6,
                "broken": [
                    {"source": "sub/index.md", "line": 2, "target": "gone.md", "reason": "missing_target"},
                    {"source": "sub/index.md", "line": 2, "target": "gone.md", "reason": "missing_target"},
                ],
            },
        )

    def test_fences_and_sorted_results(self):
        self.write(
            "z.md",
            "~~~\n[ignored](x.md)\n```\n[still ignored](x.md)\n~~~~\n"
            "[last](z-missing.md)\n",
        )
        self.write("a.md", "[two](z-missing.md) [one](a-missing.md)\n")
        result = mdcheck.check(self.root)
        self.assertEqual(result.checked, 3)
        self.assertEqual(
            [(item.source, item.line, item.target) for item in result.broken],
            [("a.md", 1, "a-missing.md"), ("a.md", 1, "z-missing.md"), ("z.md", 6, "z-missing.md")],
        )

    def test_file_input_text_and_success(self):
        page = self.write("nested/page.md", "[there](target.md)\n")
        self.write("nested/target.md", "# There\n")
        result = self.cli(page)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Checked 1 local link(s); found 0 broken link(s).", result.stdout)
        self.assertEqual(mdcheck.check(page).checked, 1)
        self.assertEqual(mdcheck.check(page).broken, ())

    def test_symlinks_not_scanned(self):
        self.write("real.md", "[ok](.)\n")
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        (outside / "bad.md").write_text("[bad](missing.md)", encoding="utf-8")
        try:
            (self.root / "external").symlink_to(outside, target_is_directory=True)
            (self.root / "link.md").symlink_to(outside / "bad.md")
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"symlinks unavailable: {error}")
        self.assertEqual(mdcheck.check(self.root).checked, 1)
        self.assertEqual(self.cli(self.root / "link.md").returncode, 2)

    def test_invalid_input_and_arguments(self):
        for args in ((self.root / "missing",), ("--format", "yaml")):
            with self.subTest(args=args):
                result = self.cli(*args)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertNotEqual(result.stderr, "")

    def test_empty_directory_json_and_angle_destination(self):
        self.write("a b.md", "# File\n")
        self.write("index.md", "[file](<a b.md>)\n")
        result = self.cli(self.root, "--format", "json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"checked": 1, "broken": []})

        empty = self.root / "empty"
        empty.mkdir()
        self.assertEqual(mdcheck.check(empty), mdcheck.CheckResult(checked=0, broken=()))


if __name__ == "__main__":
    unittest.main()
