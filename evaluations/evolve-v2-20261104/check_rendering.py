"""Compare the extracted seed text to evolve2-base, without model calls."""
import io
import itertools
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile

REPO = Path(__file__).resolve().parents[2]


def snapshot():
    from aster.context.prompt import system_prompt, workflow_state
    from aster.core.recovery import completion_feedback
    from aster.schema import RunState, TaskSpec, Verification
    from aster.tools.schemas import definitions

    result = {"definitions": definitions()}
    for memory, skills, worker, workers in itertools.product((False, True), repeat=4):
        result[f"system/{memory}/{skills}/{worker}/{workers}"] = system_prompt(
            memory, skills, "fixed environment", object(), worker=worker,
            workers_available=workers,
        )
    for case in ("pending", "identical", "both", "missing_tests", "passed", "accepted"):
        for changed in (False, True):
            state = RunState(task=TaskSpec(prompt="Fix the regression.", require_verification=True))
            state.changed_paths = ["example.py"] if changed else []
            if case in ("pending", "both", "accepted"):
                state.verification_failures["test_new"] = "pytest tests/test_new.py"
            if case in ("identical", "both"):
                state.verification_failures["test_old"] = "pytest tests/test_old.py"
                state.preexisting_failures["test_old"] = "same signature"
            if case == "passed":
                state.verification = Verification(state="passed", command="pytest")
            accepted = {"test_new": "already accounted for"} if case == "accepted" else None
            result[f"completion/{case}/{changed}"] = completion_feedback(state, accepted)
            result[f"workflow/{case}/{changed}"] = workflow_state(state)
    return result


if __name__ == "__main__":
    if "--snapshot" in sys.argv:
        print(json.dumps(snapshot(), sort_keys=True, ensure_ascii=False))
    else:
        archive = subprocess.check_output(["git", "archive", "evolve2-base", "src/aster"], cwd=REPO)
        with tempfile.TemporaryDirectory(prefix="evolve2-render-") as directory:
            with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
                bundle.extractall(directory, filter="data")
            before = subprocess.check_output(
                [sys.executable, str(Path(__file__).resolve()), "--snapshot"], cwd=directory,
                env={**os.environ, "PYTHONPATH": str(Path(directory) / "src")},
            )
        after = subprocess.check_output(
            [sys.executable, str(Path(__file__).resolve()), "--snapshot"], cwd=REPO,
            env={**os.environ, "PYTHONPATH": str(REPO / "src")},
        )
        assert before == after, "rendered seed output changed"
        for name in json.loads(after):
            print(f"SAME {name}")
        print(f"PASS: {len(json.loads(after))} byte-identical outputs")
