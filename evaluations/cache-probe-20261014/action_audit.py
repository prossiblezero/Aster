"""Offline: classify every tool action by purpose for Aster vs pi vs Codex on the same tasks."""
import collections
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "context-cache-20261013"))
from metrics import requests  # noqa: E402

DISCOVER = re.compile(r"^\s*(cd [^&;]+(&&|;)\s*)?(cat|sed|rg|grep|ls|find|head|tail|nl|wc|tree|git (show|log|grep|ls-files|status))\b")
TEST = re.compile(r"(pytest|python3? -m (pytest|unittest)|tox|make test|cargo test|go test|npm test|\./test|bash .*test|runtests|tests?/)")
DIFF = re.compile(r"^\s*(cd [^&;]+(&&|;)\s*)?git (diff|status)")


def classify(agent, name, args):
    if agent == "aster":
        return {"read_file": "discover", "search": "discover", "list_files": "discover",
                "git_diff": "review_diff", "patch_file": "edit", "write_file": "edit",
                "run_tests": "test", "observation_read": "discover"}.get(name) or (
            "test" if name == "run_shell" and TEST.search(args.get("command", "")) else
            "discover" if name == "run_shell" and DISCOVER.search(args.get("command", "")) else
            "review_diff" if name == "run_shell" and DIFF.search(args.get("command", "")) else
            "shell_other" if name == "run_shell" else "other:" + name)
    if agent == "pi":
        if name == "read":
            return "discover"
        if name in ("edit", "write"):
            return "edit"
        command = args.get("command", "")
        return ("review_diff" if DIFF.search(command) else "test" if TEST.search(command)
                else "discover" if DISCOVER.search(command) else "shell_other")
    return "exec"


def audit(runs, agent, tasks):
    kinds, per_task = collections.Counter(), {}
    for task in tasks:
        items = requests(runs / f"{agent}-{task}" / "provider")[-1][1]["input"]
        n = 0
        for x in items:
            if x.get("type") == "function_call":
                try:
                    args = json.loads(x.get("arguments") or "{}")
                except ValueError:
                    args = {}
                kinds[classify(agent, x["name"], args)] += 1
                n += 1
            elif x.get("type") == "custom_tool_call":
                kinds["exec"] += 1
                n += 1
        per_task[task] = n
    return kinds, per_task


if __name__ == "__main__":
    new, old = Path(sys.argv[1]), Path(sys.argv[2])
    tasks = sorted(d.parent.name[6:] for d in new.glob("aster-*/provider"))
    out = {}
    for agent, runs in (("aster", new), ("pi", old), ("codex", old)):
        kinds, per_task = audit(runs, agent, tasks)
        out[agent] = {"actions": sum(kinds.values()), "kinds": dict(kinds.most_common()), "per_task": per_task}
        print(agent, sum(kinds.values()), kinds.most_common())
    Path(__file__).with_name("action-audit.json").write_text(json.dumps(out, indent=2))
