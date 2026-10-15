"""Offline: where do Aster's extra model turns go, compared with pi and Codex (old20)."""
import collections
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "context-cache-20261013"))
from metrics import requests  # noqa: E402

NEW = Path(sys.argv[1])        # v9 runs
OLD = Path(sys.argv[2])        # 50-task runs (pi, codex)
v9 = {r["task"]: r for r in json.load(open(HERE.parent / "context-cache-20261013/results.json"))}
base = {(r["agent"], r["task"]): r for r in json.load(open(HERE.parent / "general-coding-50-20261012/results.json"))}
tasks = sorted(v9)
both = [t for t in tasks if v9[t]["passed"] and base[("pi", t)]["passed"] and base[("codex", t)]["passed"]]


def transcript(runs, agent, task):
    rs = requests(runs / f"{agent}-{task}" / "provider")
    return len(rs), rs[-1][1]["input"]


def turns(items):
    """Split the final transcript into model turns: each run of model items between inputs."""
    out, cur = [], []
    for x in items:
        t = x.get("type") or x.get("role")
        if t in ("reasoning", "message", "function_call", "custom_tool_call", "assistant"):
            cur.append(x)
        elif cur:
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return out


print("tasks", len(tasks), "all-three-passed", len(both))
for label, sel in (("all old20", tasks), ("all three passed", both)):
    for agent, runs in (("aster", NEW), ("pi", OLD), ("codex", OLD)):
        print(f"{label:17s} {agent:5s} requests={sum(transcript(runs, agent, t)[0] for t in sel)}")

# Aster turn taxonomy on all old20
kinds = collections.Counter()
reread = collections.Counter()
ranges = []
user_msgs = collections.Counter()
single_read_turns = 0
for t in tasks:
    n, items = transcript(NEW, "aster", t)
    seen = collections.defaultdict(list)
    for x in items:
        if x.get("role") == "user" and isinstance(x.get("content"), str):
            head = x["content"].split("\n", 1)[0][:60]
            user_msgs[head.split(":")[0]] += 1
    for turn in turns(items):
        calls = [x for x in turn if x.get("type") == "function_call"]
        names = tuple(sorted({c["name"] for c in calls}))
        if not calls:
            kinds["no_call (final/commentary)"] += 1
            continue
        if len(calls) == 1:
            kinds["single:" + calls[0]["name"]] += 1
        else:
            kinds["multi:" + "+".join(names)] += 0
            kinds[f"multi({len(calls)})"] += 1
        for c in calls:
            if c["name"] == "read_file":
                a = json.loads(c["arguments"])
                seen[a["path"]].append((a.get("start"), a.get("end")))
                if a.get("start") and a.get("end"):
                    ranges.append(a["end"] - a["start"] + 1)
    for path, rr in seen.items():
        reread["paths"] += 1
        reread["reads"] += len(rr)
        reread["paths_read_3plus"] += len(rr) >= 3
print("aster turn kinds:", kinds.most_common(14))
print("aster read_file: paths", reread["paths"], "reads", reread["reads"], "paths read >=3 times", reread["paths_read_3plus"])
ranges.sort()
print("read_file requested lines median/p25/p75", ranges[len(ranges)//2], ranges[len(ranges)//4], ranges[3*len(ranges)//4])
print("injected user message heads:", user_msgs.most_common(8))
