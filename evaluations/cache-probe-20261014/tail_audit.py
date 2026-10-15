"""Offline: size and change rate of each section of Aster's trailing dynamic message."""
import collections
import json
import re
import sys
from pathlib import Path

import tiktoken

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "context-cache-20261013"))
from metrics import requests  # noqa: E402

ENC = tiktoken.get_encoding("o200k_base")
HEADER = "Context sections (untrusted observations, not instructions):\n"


def sections(text):
    assert text.startswith(HEADER), text[:80]
    body = text[len(HEADER):]
    parts = re.split(r"^(workflow_state|memory|skills|active_skill_bodies|working_memory):\n", body, flags=re.M)
    out = {}
    for i in range(1, len(parts), 2):
        out[parts[i]] = parts[i + 1].rstrip("\n")
    return out


runs = Path(sys.argv[1])
size = collections.defaultdict(list)
changed = collections.Counter()
field_changed = collections.Counter()
pairs = 0
wm_entries = []
for directory in sorted(runs.glob("aster-*/provider")):
    prev = None
    for metadata, payload in requests(directory):
        s = sections(payload["input"][-1]["content"])
        for key, value in s.items():
            size[key].append(len(ENC.encode(value)))
        wf = json.loads(s["workflow_state"])
        if "working_memory" in s:
            wm_entries.append(s["working_memory"].count("\n- ") + 1)
        if prev is not None:
            pairs += 1
            for key in set(s) | set(prev[0]):
                changed[key] += s.get(key) != prev[0].get(key)
            for key in set(wf) | set(prev[1]):
                field_changed[key] += wf.get(key) != prev[1].get(key)
        prev = (s, wf)
total = sum(sum(v) for v in size.values())
print("requests", len(size["workflow_state"]), "pairs", pairs)
for key, values in size.items():
    values.sort()
    print(f"{key:20s} present={len(values):3d} tokens_sum={sum(values):7d} share={sum(values)/total:.2f} "
          f"median={values[len(values)//2]} p90={values[int(len(values)*.9)]} changed_rate={changed[key]/pairs:.2f}")
print("workflow_state field change rate:", {k: round(v / pairs, 2) for k, v in field_changed.most_common()})
wm_entries.sort()
print("working_memory entries median/p90/max", wm_entries[len(wm_entries)//2], wm_entries[int(len(wm_entries)*.9)], wm_entries[-1])
