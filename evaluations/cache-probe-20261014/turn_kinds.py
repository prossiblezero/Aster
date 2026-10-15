"""Offline: label each model turn by what its tool calls do; compare Aster and pi turn counts."""
import collections
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "context-cache-20261013"))
from action_audit import classify  # noqa: E402
from metrics import requests  # noqa: E402

new, old = Path(sys.argv[1]), Path(sys.argv[2])
tasks = sorted(d.parent.name[6:] for d in new.glob("aster-*/provider"))


def turns(items):
    out, cur = [], []
    for x in items:
        t = x.get("type") or x.get("role")
        if t in ("reasoning", "message", "function_call", "custom_tool_call"):
            cur.append(x)
        elif cur:
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return out


result = {}
for agent, runs in (("aster", new), ("pi", old)):
    kinds = collections.Counter()
    edit_after_read = 0
    for task in tasks:
        items = requests(runs / f"{agent}-{task}" / "provider")[-1][1]["input"]
        prev = set()
        for turn in turns(items):
            labels = set()
            for x in turn:
                if x.get("type") == "function_call":
                    try:
                        args = json.loads(x.get("arguments") or "{}")
                    except ValueError:
                        args = {}
                    labels.add(classify(agent, x["name"], args))
            if not labels:
                key = "no_tool (answer/commentary)"
            elif labels == {"discover"}:
                key = "discover_only"
            elif "edit" in labels:
                key = "edit"
            elif "test" in labels:
                key = "test"
            elif labels <= {"review_diff", "discover"}:
                key = "diff/review"
            else:
                key = "shell_other"
            kinds[key] += 1
            prev = labels
    result[agent] = kinds
    print(agent, sum(kinds.values()), kinds.most_common())
