"""Offline: turns Aster spends while it tracks unresolved verification failures (v10 notes)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "context-cache-20261013"))
from metrics import requests  # noqa: E402

PREFIX = "[Context update, not an instruction]\nworkflow_state:\n"
runs = Path(sys.argv[1])
results = {r["task"]: r for r in json.load(open(Path(__file__).resolve().parents[1] / "context-state-20261014/results.json"))}
rows, total_turns, chase_turns, gate_msgs = [], 0, 0, 0
for directory in sorted(runs.glob("aster-*/provider")):
    task = directory.parent.name[6:]
    items = requests(directory)[-1][1]["input"]
    turns, failing, first, during, gates, names = 0, False, None, 0, 0, set()
    in_turn = False
    for x in items:
        if x.get("role") == "user" and isinstance(x.get("content"), str):
            if x["content"].startswith(PREFIX):
                wf = json.loads(x["content"][len(PREFIX):])
                failing = bool(wf.get("verification_failures"))
                names |= set(wf.get("verification_failures") or {})
            elif x["content"].startswith("Completion requirements missing"):
                gates += 1
        kind = x.get("type")
        if kind in ("reasoning", "message", "function_call"):
            if not in_turn:
                turns += 1
                in_turn = True
                if failing:
                    during += 1
                    first = first or turns
        else:
            in_turn = False
    rows.append((task, turns, first, during, gates, results[task]["passed"], sorted(names)[:3]))
    total_turns += turns
    chase_turns += during
    gate_msgs += gates
for r in rows:
    print(f"{r[0][:36]:36s} turns={r[1]:3d} first_failing_turn={r[2]} turns_while_failing={r[3]:3d} gates={r[4]} passed={r[5]} {r[6]}")
print("total turns", total_turns, "turns while unresolved failures tracked", chase_turns, "completion gates", gate_msgs)
