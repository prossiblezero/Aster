"""Official prediction export preserves empty attempts and rejects unknown tasks."""

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "evaluations/swebench/predictions.py"


def test_prediction_cli_preserves_empty_attempts_and_rejects_unknown_ids(tmp_path):
    patches = tmp_path / "patches"
    patches.mkdir()
    instance = json.loads((REPO / "datasets/swebench-verified/selection.json").read_text())["instances"][0]["instance_id"]
    (patches / f"{instance}.patch").write_text("")
    output = tmp_path / "predictions.jsonl"
    command = [sys.executable, str(SCRIPT), "--patches", str(patches),
               "--model-id", "aster-test", "--output", str(output)]
    subprocess.run(command, check=True, capture_output=True)
    assert json.loads(output.read_text()) == dict(instance_id=instance,
                                                  model_name_or_path="aster-test", model_patch="")
    original = output.read_bytes()
    (patches / "unknown.patch").write_text("not a known task")
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode != 0 and "outside the fixed subset" in result.stderr
    assert output.read_bytes() == original
