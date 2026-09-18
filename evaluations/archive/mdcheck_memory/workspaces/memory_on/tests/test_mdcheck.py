from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import mdcheck


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class MdcheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def write(self, relative_path: str, content: str = "") -> Path:
        path = self.root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def run_cli(
        self, *arguments: object, cwd: Path = PROJECT_ROOT
    ) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment["PYTHONPATH"] = os.pathsep.join(
            filter(None, (str(PROJECT_ROOT), environment.get("PYTHONPATH", "")))
        )
        return subprocess.run(
            [sys.executable, "-m", "mdcheck", *(str(item) for item in arguments)],
            cwd=cwd,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_default_manual_and_explicit_old_directory(self) -> None:
        self.write("manual/start.md", "# Start\n[section](#start)\n[missing](absent.txt)\n")
        self.write("docs/legacy.md", "# Legacy\n[section](#legacy)\n")

        default = self.run_cli("--format", "json", cwd=self.root)
        old_directory = self.run_cli("docs", "--format", "json", cwd=self.root)

        self.assertEqual(default.returncode, 1)
        self.assertEqual(default.stderr, "")
        self.assertEqual(
            json.loads(default.stdout),
            {
                "checked": 2,
                "broken": [
                    {
                        "source": "start.md",
                        "line": 3,
                        "target": "absent.txt",
                        "reason": "missing_target",
                    }
                ],
            },
        )
        self.assertEqual(old_directory.returncode, 0)
        self.assertEqual(old_directory.stderr, "")
        self.assertEqual(json.loads(old_directory.stdout), {"checked": 1, "broken": []})

    def test_json_reports_counts_fields_and_stable_order(self) -> None:
        self.write(
            "z.md",
            "[later](z-missing.txt)\n[again](same.txt) [again](same.txt)\n",
        )
        self.write("nested/a.md", "![image](a-missing.png)\n")

        result = self.run_cli(self.root, "--format", "json")

        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            json.loads(result.stdout),
            {
                "checked": 4,
                "broken": [
                    {
                        "source": "nested/a.md",
                        "line": 1,
                        "target": "a-missing.png",
                        "reason": "missing_target",
                    },
                    {
                        "source": "z.md",
                        "line": 1,
                        "target": "z-missing.txt",
                        "reason": "missing_target",
                    },
                    {
                        "source": "z.md",
                        "line": 2,
                        "target": "same.txt",
                        "reason": "missing_target",
                    },
                    {
                        "source": "z.md",
                        "line": 2,
                        "target": "same.txt",
                        "reason": "missing_target",
                    },
                ],
            },
        )

    def test_valid_local_targets_utf8_percent_encoding_and_fragments(self) -> None:
        self.write("文档/目标 文件.txt")
        self.write("文档/目标(文件).txt")
        self.write(
            "文档/readme.md",
            "\n".join(
                (
                    "[encoded](%E7%9B%AE%E6%A0%87%20%E6%96%87%E4%BB%B6.txt)",
                    "[fragment](目标%20文件.txt#section)",
                    "[directory](../文档/)",
                    "[angle](<目标 文件.txt> \"title\")",
                    "[escaped](目标\\(文件\\).txt)",
                )
            ),
        )

        checked, broken = mdcheck.check(self.root)

        self.assertEqual(checked, 5)
        self.assertEqual(broken, [])

    def test_skips_external_links_and_fenced_code(self) -> None:
        markdown = self.write(
            "readme.md",
            "\n".join(
                (
                    "# Heading",
                    "[web](https://example.com/x)",
                    "[mail](mailto:user@example.com)",
                    "[host](//example.com/x)",
                    "[anchor](#heading)",
                    "```python",
                    "# Not an anchor",
                    "[missing](inside-backticks.txt)",
                    "`````",
                    "~~~~",
                    "![missing](inside-tildes.png)",
                    "~~~~",
                    "[real](exists.txt)",
                )
            ),
        )
        self.write("exists.txt")

        result = self.run_cli(markdown, "--format", "json")

        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout), {"checked": 2, "broken": []})

    def test_checks_atx_anchors_percent_decoding_and_duplicate_slugs(self) -> None:
        self.write(
            "guide.md",
            "\n".join(
                (
                    "# Install & Setup!",
                    "## Install & Setup!",
                    "###### Install & Setup!",
                    "### 标题 示例",
                    "#### under_score and-hyphen",
                )
            ),
        )
        self.write(
            "index.md",
            "\n".join(
                (
                    "[first](guide.md#install-setup)",
                    "[duplicate](guide.md#install-setup-1)",
                    "[third](guide.md#install-setup-2)",
                    "[unicode](guide.md#%E6%A0%87%E9%A2%98-%E7%A4%BA%E4%BE%8B)",
                    "[punctuation](guide.md#under_score-and-hyphen)",
                    "[empty fragment](guide.md#)",
                )
            ),
        )

        checked, broken = mdcheck.check(self.root)

        self.assertEqual(checked, 6)
        self.assertEqual(broken, [])

    def test_reports_missing_anchors_for_pure_and_markdown_links(self) -> None:
        self.write("other.md", "# Present\n")
        page = self.write(
            "page.md",
            "\n".join(
                (
                    "# Local",
                    "[missing local](#absent)",
                    "[missing remote](other.md#absent)",
                    "[present local](#local)",
                )
            ),
        )

        result = self.run_cli(page, "--format", "json")

        self.assertEqual(result.returncode, 1)
        self.assertEqual(
            json.loads(result.stdout),
            {
                "checked": 3,
                "broken": [
                    {
                        "source": "page.md",
                        "line": 2,
                        "target": "#absent",
                        "reason": "missing_anchor",
                    },
                    {
                        "source": "page.md",
                        "line": 3,
                        "target": "other.md#absent",
                        "reason": "missing_anchor",
                    },
                ],
            },
        )

    def test_non_markdown_fragments_only_require_existing_path(self) -> None:
        self.write("asset.txt")
        self.write(
            "page.md",
            "[unchecked anchor](asset.txt#absent) [missing](absent.txt#anchor)\n",
        )

        checked, broken = mdcheck.check(self.root)

        self.assertEqual(checked, 2)
        self.assertEqual(
            broken,
            [mdcheck.BrokenLink("page.md", 1, "absent.txt#anchor", "missing_target")],
        )

    def test_only_atx_headings_outside_fences_create_anchors(self) -> None:
        page = self.write(
            "page.md",
            "\n".join(
                (
                    "# Valid",
                    "No ATX heading",
                    "---",
                    "   # Indented",
                    "####### Too many",
                    "#Missing space",
                    "```",
                    "# Fenced",
                    "```",
                    "[links](#valid) [setext](#no-atx-heading) [fenced](#fenced)",
                )
            ),
        )

        checked, broken = mdcheck.check(page)

        self.assertEqual(checked, 3)
        self.assertEqual(
            broken,
            [
                mdcheck.BrokenLink("page.md", 10, "#fenced", "missing_anchor"),
                mdcheck.BrokenLink("page.md", 10, "#no-atx-heading", "missing_anchor"),
            ],
        )

    def test_relative_targets_are_resolved_from_source_directory(self) -> None:
        self.write("shared.txt")
        self.write("nested/page.md", "[up](../shared.txt) [local](local.txt)\n")
        self.write("nested/local.txt")

        checked, broken = mdcheck.check(self.root)

        self.assertEqual(checked, 2)
        self.assertEqual(broken, [])

    def test_text_output_and_single_file_source(self) -> None:
        markdown = self.write("page.md", "first\n[bad](missing.txt)\n")

        result = self.run_cli(markdown)

        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            result.stdout,
            "Checked 1 local link(s); found 1 broken.\n"
            "page.md:2: missing.txt (missing_target)\n",
        )

    def test_missing_root_is_an_operational_error(self) -> None:
        result = self.run_cli(self.root / "absent", "--format", "json")

        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("root does not exist", result.stderr)

    def test_invalid_format_is_an_argument_error(self) -> None:
        result = self.run_cli(self.root, "--format", "xml")

        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("invalid choice", result.stderr)

    def test_non_markdown_file_root_checks_nothing(self) -> None:
        text = self.write("notes.txt", "[bad](missing.txt)\n")

        result = self.run_cli(text, "--format", "json")

        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout), {"checked": 0, "broken": []})

    @unittest.skipUnless(hasattr(os, "symlink"), "symbolic links are unavailable")
    def test_symlinked_files_and_directories_are_not_scanned(self) -> None:
        outside = self.write("outside/bad.md", "[bad](missing.txt)\n")
        scan = self.root / "scan"
        scan.mkdir()
        try:
            (scan / "linked.md").symlink_to(outside)
            (scan / "linked-dir").symlink_to(outside.parent, target_is_directory=True)
        except OSError as error:
            self.skipTest(f"cannot create symbolic links: {error}")
        self.write("scan/real.md", "[ok](real.md)\n")

        checked, broken = mdcheck.check(scan)

        self.assertEqual(checked, 1)
        self.assertEqual(broken, [])


if __name__ == "__main__":
    unittest.main()
