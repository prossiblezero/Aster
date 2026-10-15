"""Summarize probe.py results.jsonl. No model calls."""
import collections
import json
import sys
from pathlib import Path

path = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).with_name("results.jsonl"))
rows = [json.loads(line) for line in path.open()]
trials = collections.defaultdict(list)
for row in rows:
    trials[(row["condition"], row["rep"])].append(row)
summary = collections.defaultdict(lambda: dict(followups=0, full=0, uncached=0, input=0))
for (condition, rep), items in sorted(trials.items()):
    items.sort(key=lambda r: r["step"])
    print(condition, rep, " ".join(f"{r['step']}:{r['input_tokens']}/{r['cached_tokens']}" for r in items))
    for a, b in zip(items, items[1:]):
        if not a["input_tokens"] or not b["input_tokens"]:
            continue
        s = summary[condition]
        s["followups"] += 1
        s["full"] += b["cached_tokens"] >= 0.85 * (a["input_tokens"] - a["tail_tokens"])
        s["uncached"] += b["input_tokens"] - b["cached_tokens"]
        s["input"] += b["input_tokens"]
print(json.dumps({c: {**s, "full_rate": round(s["full"] / s["followups"], 3),
                      "hit": round(1 - s["uncached"] / s["input"], 3)}
                  for c, s in summary.items()}, indent=2))
print("status:", dict(collections.Counter(f"{r['status']}/{r['terminal']}" for r in rows)))
