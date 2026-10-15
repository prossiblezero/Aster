"""Measure real baseline/candidate persistence, including checked baseline recovery."""
import argparse
import json
import statistics
import tarfile
import tempfile
import time
from pathlib import Path

from aster.safety.redaction import Redactor
from aster.schema import RunState
from aster.sessions.checkpoint import save_checkpoint
from aster.sessions.tree import SessionTree
from aster.storage.evidence import EvidenceStore

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
SOURCE = REPO / "artifacts/private/general-coding-cost-20261011/full-candidate/runs/aster-django__django-17029/.aster.tar"


def main(label):
    with tarfile.open(SOURCE) as archive:
        member = next(m for m in archive.getmembers() if m.name.endswith("/checkpoint.json"))
        data = json.load(archive.extractfile(member))
    with tempfile.TemporaryDirectory(prefix="aster-checkpoint-profile-") as directory:
        evidence = EvidenceStore(Path(directory), Redactor())
        tree = SessionTree(evidence)
        state = RunState.model_validate(data["state"])
        arguments = {name: data.get(name) for name in (
            "runtime_identity", "structured_history", "initial_text", "initial_modes",
            "recovery_used", "stall_items", "archive_sources", "history_origins", "continued_from",
        )}
        timings = []
        for i in range(6):
            state.turn += 1
            started = time.perf_counter()
            save_checkpoint(evidence, state, workspace_hash=data["workspace_hash"],
                            workspace_snapshot=data["workspace"], commit=tree.commit, **arguments)
            timings.append(time.perf_counter() - started)
        stored = json.loads(evidence.path("runs", state.run_id, "checkpoint.json").read_text())
        restored = tree.restore(tree.cursor)
        for item in (stored, restored):
            initial = evidence.read_diff_baseline(item["baseline_ref"]) if "baseline_ref" in item else item
            assert initial["initial_text"] == data["initial_text"]
            assert initial["initial_modes"] == data["initial_modes"]
            assert item["structured_history"] == data["structured_history"]
            assert item["state"]["turn"] == state.turn
        result = {"label": label, "task": data["state"]["task"].get("id"),
                  "initial_text_characters": sum(map(len, data["initial_text"].values())),
                  "first_save_seconds": timings[0], "subsequent_seconds": timings[1:],
                  "subsequent_median_seconds": statistics.median(timings[1:]),
                  "checkpoint_bytes": evidence.path("runs", state.run_id, "checkpoint.json").stat().st_size,
                  "database_bytes": evidence.path("sessions.sqlite3").stat().st_size,
                  "baseline_files": len(list(evidence.path("runs").glob("*/baselines/*.json"))),
                  "json_and_tree_recovery_verified": True,
                  "scope": "Six real persistence updates; excludes workspace scanning and provider time."}
    (ROOT / ("profile-" + label + ".json")).write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("label", choices=("baseline", "candidate"))
    main(parser.parse_args().label)
