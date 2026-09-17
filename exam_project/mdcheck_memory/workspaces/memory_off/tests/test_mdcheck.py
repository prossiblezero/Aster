"""End-to-end and API checks for the mdcheck CLI."""

import json
import os
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

    def cli(self, *args, cwd=None):
        project_root = Path(__file__).resolve().parents[1]
        environment = os.environ.copy()
        python_path = environment.get("PYTHONPATH")
        environment["PYTHONPATH"] = str(project_root) + (
            os.pathsep + python_path if python_path else ""
        )
        return subprocess.run(
            [sys.executable, "-m", "mdcheck", *map(str, args)],
            cwd=cwd or project_root,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )

    def test_default_manual_and_explicit_docs_root(self):
        sandbox = Path(self.temp.name)
        manual = sandbox / "manual"
        manual.mkdir()
        (manual / "index.md").write_text(
            "# Manual\n[section](#manual)\n", encoding="utf-8"
        )
        self.write("index.md", "# Docs\n[section](#docs) [directory](.)\n")

        default_result = self.cli("--format", "json", cwd=sandbox)
        explicit_result = self.cli("docs", "--format", "json", cwd=sandbox)

        self.assertEqual(mdcheck.DEFAULT_ROOT, Path("manual"))
        self.assertEqual(default_result.returncode, 0, default_result.stderr)
        self.assertEqual(default_result.stderr, "")
        self.assertEqual(
            json.loads(default_result.stdout), {"checked": 1, "broken": []}
        )
        self.assertEqual(explicit_result.returncode, 0, explicit_result.stderr)
        self.assertEqual(explicit_result.stderr, "")
        self.assertEqual(
            json.loads(explicit_result.stdout), {"checked": 2, "broken": []}
        )

    def test_json_paths_images_fragments_and_duplicates(self):
        self.write("sub/guide one.md", "# Missing\n")
        self.write("sub/assets/icon.png", "image")
        self.write(
            "sub/index.md",
            "[ok](guide%20one.md#missing) ![icon](assets/icon.png) "
            "[dir](../) [unicode](caf%C3%A9.md)\n"
            "[bad](gone.md) [bad again](gone.md) [fragment](#section)\n"
            "[web](https://example.org/no.md) [mail](mailto:x@y.z) "
            "[cdn](//host/no.md)\n"
            "# Section\n",
        )
        self.write("sub/café.md", "# Café\n")
        result = self.cli(self.root, "--format", "json")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            json.loads(result.stdout),
            {
                "checked": 7,
                "broken": [
                    {"source": "sub/index.md", "line": 2, "target": "gone.md", "reason": "missing_target"},
                    {"source": "sub/index.md", "line": 2, "target": "gone.md", "reason": "missing_target"},
                ],
            },
        )

    def test_atx_anchors_percent_decoding_and_duplicate_slugs(self):
        self.write(
            "guide.md",
            "# Hello, World!\n"
            "## Repeat_Name\n"
            "## Repeat_Name\n"
            "### 中文 标题 ###\n"
            "#### Mixed—Punctuation!\n"
            "Not an ATX heading\n"
            "```\n# Hidden Heading\n```\n",
        )
        self.write(
            "index.md",
            "# Current Page\n"
            "[same](#current-page) [empty](#)\n"
            "[punctuation](guide.md#hello-world) "
            "[duplicate](guide.md#repeat_name-1)\n"
            "[unicode](guide.md#%E4%B8%AD%E6%96%87-%E6%A0%87%E9%A2%98) "
            "[non-ascii punctuation](guide.md#mixed—punctuation)\n"
            "[missing duplicate](guide.md#repeat_name-2) "
            "[hidden](guide.md#hidden-heading)\n",
        )

        result = mdcheck.check(self.root)

        self.assertEqual(result.checked, 8)
        self.assertEqual(
            [(item.source, item.line, item.target, item.reason) for item in result.broken],
            [
                ("index.md", 5, "guide.md#hidden-heading", "missing_anchor"),
                ("index.md", 5, "guide.md#repeat_name-2", "missing_anchor"),
            ],
        )

    def test_anchor_validation_scope_and_missing_target_precedence(self):
        self.write("asset.txt", "plain text")
        self.write("folder.md/item.txt", "plain text")
        self.write(
            "index.md",
            "[non-markdown](asset.txt#anything) [directory](folder.md#anything)\n"
            "[missing path](missing.md#anchor) [empty fragment](asset.txt#)\n",
        )

        result = mdcheck.check(self.root)

        self.assertEqual(result.checked, 4)
        self.assertEqual(
            [(item.target, item.reason) for item in result.broken],
            [("missing.md#anchor", "missing_target")],
        )

    def test_pure_fragments_exact_matching_and_json_anchor_errors(self):
        page = self.write(
            "page.md",
            "# Repeat\n## Repeat\n###### Repeat\n"
            "[second](#repeat-1) [third](#repeat-2) [empty](#)\n"
            "[case](#Repeat) [missing](#absent) [missing again](#absent)\n"
            "~~~\n# Fenced\n[ignored](#absent)\n~~~~\n",
        )
        result = self.cli(page, "--format", "json")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            json.loads(result.stdout),
            {
                "checked": 6,
                "broken": [
                    {"source": "page.md", "line": 5, "target": target,
                     "reason": "missing_anchor"}
                    for target in ("#Repeat", "#absent", "#absent")
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
