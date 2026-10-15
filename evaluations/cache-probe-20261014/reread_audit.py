"""Offline: why Aster issues more discovery actions. Classifies each read_file/search call."""
import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "context-cache-20261013"))
from metrics import requests  # noqa: E402

runs = Path(sys.argv[1])
total = collections.Counter()
for directory in sorted(runs.glob("aster-*/provider")):
    items = requests(directory)[-1][1]["input"]
    outputs = {x["call_id"]: x.get("output", "") for x in items if x.get("type") == "function_call_output"}
    reads = collections.defaultdict(list)       # path -> list of (start, end) since last edit
    edited_since_read = set()
    ever_read = set()
    for x in items:
        if x.get("type") != "function_call":
            continue
        try:
            args = json.loads(x.get("arguments") or "{}")
        except ValueError:
            args = {}
        out = outputs.get(x["call_id"], "")
        status = ""
        try:
            status = json.loads(out).get("reason", "") if isinstance(out, str) and out.startswith("{") else ""
        except ValueError:
            pass
        name = x["name"]
        if name in ("patch_file", "write_file"):
            total[name] += 1
            if status == "fresh_read_required":
                total["edit_blocked_fresh_read_required"] += 1
                continue
            path = args.get("path")
            edited_since_read.add(path)
            reads[path] = []
        elif name == "read_file":
            path = args.get("path")
            start, end = args.get("start") or 1, args.get("end") or 10**9
            total["read_file"] += 1
            if path in edited_since_read:
                total["read:after_own_edit"] += 1
                edited_since_read.discard(path)
            elif any(s <= start and end <= e for s, e in reads[path]):
                total["read:fully_covered_repeat"] += 1
            elif any(not (end < s or start > e) for s, e in reads[path]):
                total["read:overlapping_repeat"] += 1
            elif any(start == e + 1 for s, e in reads[path]):
                total["read:next_adjacent_chunk"] += 1
            elif path in ever_read:
                total["read:other_range_same_file"] += 1
            else:
                total["read:first_read_of_file"] += 1
            reads[path].append((start, end))
            ever_read.add(path)
        elif name == "search":
            total["search"] += 1
            total["search:paged" if args.get("offset") or args.get("cursor") else "search:new"] += 1
        elif name == "list_files":
            total["list_files"] += 1
for k, v in sorted(total.items()):
    print(f"{k:34s} {v}")
