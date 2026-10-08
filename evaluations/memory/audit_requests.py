"""Check saved foreground request prefixes without making model calls."""

import argparse
import json
import zipfile
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parents[2] / "artifacts/memory"


def audit(arm, *, result_root=None, archive_path=None):
    episodes = []
    result_root = result_root or HERE / "results" / arm
    archive_path = archive_path or HERE / f"{arm}-workspaces.zip"
    if not archive_path.exists():
        return {"status": "unavailable", "totals": None, "episodes": [],
                "reason": "Full request archive was not retained; do not treat missing requests as zero."}
    with zipfile.ZipFile(archive_path) as archive:
        names = archive.namelist()
        for path in sorted(result_root.glob("*/*.json")):
            if path.stem != "reuse" and not path.stem.startswith("learn-"):
                continue
            row = json.loads(path.read_text())
            prefix = f"{path.parent.name}/.aster/runs/{row['run_id']}/requests/"
            files = sorted(name for name in names if name.startswith(prefix) and name.endswith(".json"))
            assert files, f"Missing prepared requests for {path}"
            counts, previous = Counter(), None
            for name in files:
                payload = json.loads(archive.read(name))["payload"]
                items = payload["input"]  # Both recorded arms use Responses.
                counts["requests"] += 1
                tail = items[-1].get("content", "")
                counts["dynamic_tail"] += isinstance(tail, str) and tail.startswith("Context sections")
                if previous is not None:
                    old = previous["input"]
                    counts["adjacent_pairs"] += 1
                    counts["system_changed"] += old[0] != items[0]
                    counts["initial_task_changed"] += old[1] != items[1]
                    counts["tools_changed"] += previous["tools"] != payload["tools"]
                    # A growing transcript preserves every old item before its dynamic tail.
                    counts["historical_prefix_changed"] += old[:-1] != items[:len(old) - 1]
                previous = payload
            episodes.append({"case": path.parent.name, "episode": path.stem, **counts})
    keys = ("requests", "dynamic_tail", "adjacent_pairs", "system_changed",
            "initial_task_changed", "tools_changed", "historical_prefix_changed")
    assert episodes, "No episodes found"
    return {"status": "available", "totals": {key: sum(row.get(key, 0) for row in episodes) for key in keys},
            "episodes": episodes}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=HERE)
    HERE = parser.parse_args().output
    HERE.mkdir(parents=True, exist_ok=True)
    result = {arm: audit(arm) for arm in ("baseline", "cost")}
    result["limits"] = (
        "Prepared Responses payloads, not server tokenization or actual cache hits. "
        "Some prepared requests may never be sent. Tool loading and history compaction can "
        "change a prefix. Provider-reported cached/input counts are in summary.json."
    )
    target = HERE / "cache-audit.json"
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(target)
    print(json.dumps({arm: result[arm]["totals"] for arm in ("baseline", "cost")}))
