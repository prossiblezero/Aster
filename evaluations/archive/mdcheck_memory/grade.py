"""External black-box checks; candidate code never receives this grader or expected results."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shlex
import shutil
import stat
import sys
import tempfile
from pathlib import Path

from aster.config import Settings
from aster.safety import linux
from aster.safety.runner import SandboxRunner

RUNS = Path(__file__).resolve().parent / "runs"
CAPTURE = """import json, subprocess, sys
result = subprocess.run([sys.executable, '-m', 'mdcheck', *sys.argv[1:]],
                        capture_output=True, text=True, timeout=10)
print(json.dumps({'exit_code': result.returncode, 'stdout': result.stdout,
                  'stderr': result.stderr}))
"""


def broken(source, line, target, reason="missing_target"):
    return {"source": source, "line": line, "target": target, "reason": reason}


def cases(stage):
    default = "manual" if stage == 3 else "docs"
    valid_files = {
        "docs/index.md": "\n".join([
            "[nested](nested/child.md)", "[encoded](hello%20world.md)",
            "![image](image.png)", "[中文](中文.md)", "[directory](nested)",
            "[external](https://example.invalid/no-network)", "![cdn](//example.invalid/image)",
            "[email](mailto:absent@example.invalid)", "[fragment](nested/child.md#child)",
            "[again](nested/child.md)", "[again](nested/child.md)",
        ]),
        "docs/nested/child.md": "# Child\n[parent](../index.md)\n",
        "docs/hello world.md": "# Space\n", "docs/中文.md": "# 中文\n",
        "docs/image.png": "image fixture",
    }
    ordering = [broken("a.md", 2, "a.txt"), broken("a.md", 2, "z.txt"),
                broken("a.md", 3, "z.txt"), broken("z.md", 1, "c.txt")]
    result = [
        {"name": "local_nested_encoded_images_external_and_duplicates", "files": valid_files,
         "args": ["docs", "--format", "json"], "checked": 9, "broken": []},
        {"name": "broken_records_and_stable_order", "files": {
            "docs/z.md": "[missing](c.txt)\n",
            "docs/a.md": "# Links\n[z](z.txt) [a](a.txt)\n[again](z.txt)\n"},
         "args": ["docs", "--format", "json"], "checked": 4, "broken": ordering},
        {"name": "file_root_and_parent_relative_path", "files": {
            "docs/deep/file.md": "[asset](../asset.txt)\n[missing](absent.txt)\n",
            "docs/asset.txt": "existing"},
         "args": ["docs/deep/file.md", "--format", "json"], "checked": 2,
         "broken": [broken("file.md", 2, "absent.txt")]},
        {"name": "matching_backtick_and_tilde_fences", "files": {
            "docs/index.md": "```\n[hidden](absent1)\n~~~\n[hidden](absent2)\n```\n"
                             "~~~~\n[hidden](absent3)\n~~~~\n[ok](asset.txt)\n",
            "docs/asset.txt": "existing"},
         "args": ["docs", "--format", "json"], "checked": 1, "broken": []},
        {"name": "skip_symlink_files_directories_and_cycles", "files": {
            "docs/real.md": "[ok](asset.txt)\n", "docs/asset.txt": "existing",
            "outside.md": "[broken](missing-outside)\n",
            "elsewhere/hidden.md": "[broken](missing-elsewhere)\n"},
         "symlinks": {"docs/link.md": "../outside.md", "docs/linked": "../elsewhere",
                      "docs/cycle": "."},
         "args": ["docs", "--format", "json"], "checked": 1, "broken": []},
        {"name": "missing_input_exit_2", "files": {},
         "args": ["not-present", "--format", "json"], "exit": 2},
        {"name": "invalid_argument_exit_2", "files": {"docs/index.md": "# Empty\n"},
         "args": ["docs", "--format", "not-a-format"], "exit": 2},
        {"name": "default_text_format", "files": {"docs/index.md": "[bad](missing.txt)\n"},
         "args": ["docs"], "exit": 1, "text": "missing.txt"},
        {"name": f"default_root_{default}", "files": {
            "docs/index.md": "[docs](missing-docs.txt)\n",
            "manual/index.md": "[manual](asset.txt)\n", "manual/asset.txt": "existing"},
         "args": ["--format", "json"], "checked": 1,
         "broken": [] if stage == 3 else [broken("index.md", 1, "missing-docs.txt")]},
        {"name": "explicit_docs_root", "files": {
            "docs/index.md": "[docs](missing-docs.txt)\n",
            "manual/index.md": "# Manual\n"},
         "args": ["docs", "--format", "json"], "checked": 1,
         "broken": [broken("index.md", 1, "missing-docs.txt")]},
    ]
    if stage == 1:
        result.append({"name": "stage_1_fragment_rules", "files": {
            "docs/index.md": "[self](#not-checked)\n[other](other.md#not-checked)\n",
            "docs/other.md": "# Other\n"},
            "args": ["docs", "--format", "json"], "checked": 1, "broken": []})
    else:
        result.append({"name": "anchors_unicode_duplicates_punctuation_and_fenced_headings", "files": {
            "docs/source.md": "# Start\n[self](#start)\n"
                              "[unicode](target.md#%E4%B8%AD%E6%96%87-%E6%A0%87%E9%A2%98)\n"
                              "[repeat](target.md#repeat-1)\n[third](target.md#repeat-2)\n"
                              "[punctuation](target.md#api_v2-ready)\n"
                              "[missing](target.md#nope)\n[fenced](target.md#hidden)\n"
                              "[image](logo.svg#not-checked)\n[empty](target.md#)\n",
            "docs/target.md": "# Repeat\n## Repeat\n### Repeat\n# 中文 标题\n"
                              "# API_v2 Ready!\n```\n# Hidden\n```\n",
            "docs/logo.svg": "<svg/>"},
            "args": ["docs", "--format", "json"], "checked": 9,
            "broken": [broken("source.md", 7, "target.md#nope", "missing_anchor"),
                       broken("source.md", 8, "target.md#hidden", "missing_anchor")]})
    return result


def snapshot(root):
    """Inspect links/special files before opening; include permission and directory changes."""
    entries = {}
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            path = Path(directory) / name
            mode = path.lstat().st_mode
            content = (os.readlink(path) if stat.S_ISLNK(mode) else
                       hashlib.sha256(path.read_bytes()).hexdigest() if stat.S_ISREG(mode) else "")
            entries[path.relative_to(root).as_posix()] = [mode, content]
    return entries


def validate(case, captured):
    stdout, stderr, code = captured["stdout"], captured["stderr"], captured["exit_code"]
    expected_exit = case.get("exit", 1 if case.get("broken") else 0)
    if type(code) is not int or code != expected_exit:
        return False, f"exit={code!r}, expected={expected_exit}; stderr={stderr[:300]!r}"
    if expected_exit == 2:
        return bool(stderr.strip()), "input/argument errors must explain the failure on stderr"
    if not stdout.strip():
        return False, "empty stdout cannot establish a successful check"
    if "text" in case:
        return case["text"] in stdout, f"text output must identify {case['text']!r}"
    try:
        data = json.loads(stdout)
    except (ValueError, TypeError):
        return False, f"stdout is not one JSON object: {stdout[:300]!r}"
    valid = isinstance(data, dict) and set(data) == {"checked", "broken"}
    valid = valid and type(data["checked"]) is int and isinstance(data["broken"], list)
    if valid:
        valid = all(isinstance(item, dict) and set(item) == {"source", "line", "target", "reason"}
                    and type(item["line"]) is int and item["line"] >= 1
                    and all(isinstance(item[key], str) for key in ("source", "target", "reason"))
                    for item in data["broken"])
    expected = {"checked": case["checked"], "broken": case["broken"]}
    if not valid or data != expected:
        return False, f"expected={json.dumps(expected, ensure_ascii=False)}; actual={stdout[:1200]!r}"
    return True, f"checked={data['checked']}, broken={len(data['broken'])}, exit={code}"


async def grade(workspace: Path, stage: int) -> dict:
    if stage not in (1, 2, 3):
        raise ValueError("stage must be 1, 2, or 3")
    workspace = Path(workspace).resolve()
    required = ("mdcheck.py", "pyproject.toml", "README.md")
    missing = [name for name in required if not (workspace / name).is_file()
               or (workspace / name).is_symlink() or not (workspace / name).stat().st_size]
    tests = [p for p in workspace.rglob("test*.py")
             if p.is_file() and not p.is_symlink() and p.stat().st_size]
    if not tests:
        missing.append("nonempty test*.py")
    checks = [{"name": "delivery_files", "passed": not missing,
               "detail": "missing: " + ", ".join(missing) if missing else "module, package, README and tests exist"}]
    if missing:
        return {"passed": False, "stage": stage, "checks": checks}
    RUNS.mkdir(parents=True, exist_ok=True)
    runner = SandboxRunner(Settings(sandbox="required", network=False, approval="never"))
    for case in cases(stage):
        with tempfile.TemporaryDirectory(prefix=f"grade-{stage}-", dir=RUNS) as directory:
            root = Path(directory)
            # The requested product is one standard-library module; no grader/test code is copied.
            for name in ("mdcheck.py", "PROJECT.md"):
                if (workspace / name).is_file() and not (workspace / name).is_symlink():
                    shutil.copy2(workspace / name, root / name)
            for name, content in case["files"].items():
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
            for name, target in case.get("symlinks", {}).items():
                (root / name).symlink_to(target)
            (root / ".tmp").mkdir()
            before = snapshot(root)
            command = shlex.join([sys.executable, "-I", "-c", CAPTURE, *case["args"]])
            # Reuse the required Linux launcher/process collector, with no publication or fallback.
            # run() would filter fixture symlinks before the candidate could actually see them.
            outcome = await runner._process(
                [sys.executable, "-I", str(Path(linux.__file__).resolve()), str(root), "off", command],
                root, 15,
            )
            unchanged = before == snapshot(root)
            passed, detail = False, f"sandbox status={outcome.status}, exit={outcome.exit_code}: {outcome.content[:800]}"
            if outcome.status == "success":
                try:
                    captured = json.loads(outcome.content)
                    passed, detail = validate(case, captured)
                except (ValueError, KeyError, TypeError):
                    detail = f"invalid subprocess capture: {outcome.content[:800]!r}"
            if not unchanged:
                passed, detail = False, detail + "; candidate modified the fixture or its code"
            checks.append({"name": case["name"], "passed": passed, "detail": detail,
                           "readonly": unchanged})
    return {"passed": all(item["passed"] for item in checks), "stage": stage, "checks": checks,
            "isolation": "required Landlock+seccomp; network off; disposable copy; no writeback",
            "limits": "Functional black-box fixtures; does not prove resistance to an actively malicious candidate."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workspace", type=Path)
    parser.add_argument("--stage", type=int, choices=(1, 2, 3), required=True)
    args = parser.parse_args()
    result = asyncio.run(grade(args.workspace, args.stage))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
