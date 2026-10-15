"""Restore official test paths individually, including paths absent at base commit."""
import json
import shlex
from pathlib import Path


def repair_script(script):
    lines = []
    for line in script.splitlines(keepends=True):
        if not line.startswith("git checkout "):
            lines.append(line)
            continue
        argv = shlex.split(line)
        commit, paths = argv[2], argv[3:]
        for path in paths:
            quoted = shlex.quote(path)
            obj = shlex.quote(commit + ":" + path)
            lines.append(
                f"if git cat-file -e {obj} 2>/dev/null; then\n"
                f"  git checkout {shlex.quote(commit)} -- {quoted} || exit 1\n"
                f"else\n  rm -f -- {quoted} || exit 1\nfi\n"
            )
    return "".join(lines)


def adapt_dataset(source, target):
    rows = json.loads(Path(source).read_text())
    for row in rows:
        row["eval_script"] = repair_script(row["eval_script"])
    Path(target).write_text(json.dumps(rows, indent=2))
