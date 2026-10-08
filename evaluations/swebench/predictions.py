"""Export locally produced patches in the official SWE-bench prediction format."""

import argparse
import json
from pathlib import Path

SELECTION = Path(__file__).resolve().parents[2] / "datasets/swebench-verified/selection.json"


def export(patches, model_id, output):
    allowed = {row["instance_id"] for row in json.loads(SELECTION.read_text())["instances"]}
    paths = sorted(patches.glob("*.patch"))
    if not paths:
        raise ValueError("no .patch files found; create a patch for each attempted instance")
    if unknown := {p.stem for p in paths} - allowed:
        raise ValueError(f"instances outside the fixed subset: {sorted(unknown)}")
    rows = [dict(instance_id=p.stem, model_name_or_path=model_id,
                 model_patch=p.read_text()) for p in paths]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))
    return len(rows)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--patches", type=Path, default=Path("artifacts/swebench/patches"))
    parser.add_argument("--model-id", required=True, help="Label including the model and Aster revision")
    parser.add_argument("--output", type=Path, default=Path("artifacts/swebench/predictions.jsonl"))
    args = parser.parse_args()
    print(f"Exported {export(args.patches, args.model_id, args.output)} attempted instances to {args.output}")
