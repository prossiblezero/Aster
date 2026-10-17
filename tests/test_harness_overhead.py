"""Per-step bookkeeping stays proportional to what changed, without weakening drift checks."""

import json
import os

import pytest

from aster.config import Settings
from aster.core.runtime import Runtime
from aster.schema import TaskSpec, ToolCall
from aster.storage import workspace as workspace_module
from aster.storage.workspace import Workspace

pytestmark = pytest.mark.core


def test_snapshot_reuses_digests_only_for_settled_files(tmp_path, monkeypatch):
    (tmp_path / "a.txt").write_text("alpha\n")
    space = Workspace(tmp_path)
    first = space.snapshot()
    # Just-written files sit inside the racy window and are never cached.
    assert space._digests == {}
    monkeypatch.setattr(workspace_module, "RACY_WINDOW_NS", -10**12)
    assert space.snapshot() == first
    assert set(space._digests) == {"a.txt"}


def test_same_size_rewrite_with_restored_mtime_is_detected(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace_module, "RACY_WINDOW_NS", -10**12)
    target = tmp_path / "a.txt"
    target.write_text("alpha\n")
    space = Workspace(tmp_path)
    before = space.snapshot()
    info = target.stat()
    target.write_text("omega\n")  # same size
    os.utime(target, ns=(info.st_atime_ns, info.st_mtime_ns))  # mtime restored; ctime cannot be
    after = space.snapshot()
    assert after["a.txt"] != before["a.txt"]
    assert Workspace.changes(before, after) == ["a.txt"]


def test_mode_change_is_detected_from_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace_module, "RACY_WINDOW_NS", -10**12)
    target = tmp_path / "run.sh"
    target.write_text("echo hi\n")
    space = Workspace(tmp_path)
    before = space.snapshot()
    target.chmod(0o755)
    assert space.snapshot()["run.sh"] != before["run.sh"]


def test_fingerprint_accepts_precomputed_snapshot(tmp_path):
    (tmp_path / "a.txt").write_text("alpha\n")
    space = Workspace(tmp_path)
    snapshot = space.snapshot()
    assert space.fingerprint(snapshot) == space.fingerprint()


@pytest.mark.asyncio
async def test_checkpoints_reference_one_stored_baseline(tmp_path):
    (tmp_path / "facts.txt").write_text("original\n")
    runtime = Runtime(tmp_path, Settings(approval="auto"))
    state = runtime.start(TaskSpec(prompt="edit facts.txt"))
    await runtime.step(ToolCall(name="read_file", arguments={"path": "facts.txt"}))
    await runtime.step(ToolCall(name="patch_file", arguments={
        "path": "facts.txt", "old_text": "original", "new_text": "changed"}))
    data = json.loads(runtime.evidence.path("runs", state.run_id, "checkpoint.json").read_text())
    assert data["checkpoint_version"] == 3
    assert "initial_text" not in data and "initial_modes" not in data
    baselines = list(runtime.evidence.path("runs", state.run_id).glob("baseline-*.json"))
    assert [p.stem for p in baselines] == ["baseline-" + data["baseline"]]
    assert json.loads(baselines[0].read_text())["initial_text"]["facts.txt"] == "original\n"

    runtime.abort()
    resumed = Runtime(tmp_path, Settings(approval="auto"))
    resumed.resume(state.run_id, adopt_workspace=True)
    assert resumed._initial_text["facts.txt"] == "original\n"


def test_tampered_baseline_is_rejected(tmp_path):
    (tmp_path / "facts.txt").write_text("original\n")
    runtime = Runtime(tmp_path, Settings(approval="auto"))
    state = runtime.start(TaskSpec(prompt="inspect facts.txt"))
    runtime.abort()
    data = json.loads(runtime.evidence.path("runs", state.run_id, "checkpoint.json").read_text())
    stored = runtime.evidence.path("runs", state.run_id, f"baseline-{data['baseline']}.json")
    content = json.loads(stored.read_text())
    content["initial_text"]["facts.txt"] = "forged\n"
    stored.write_text(json.dumps(content))
    data["state"]["phase"] = "running"
    runtime.evidence.path("runs", state.run_id, "checkpoint.json").write_text(json.dumps(data))
    with pytest.raises(ValueError, match="checkpoint_baseline_digest_mismatch"):
        Runtime(tmp_path, Settings(approval="auto")).resume(state.run_id)


def test_version_two_checkpoint_with_inline_baseline_still_resumes(tmp_path):
    (tmp_path / "facts.txt").write_text("original\n")
    runtime = Runtime(tmp_path, Settings(approval="auto"))
    state = runtime.start(TaskSpec(prompt="inspect facts.txt"))
    runtime.abort()
    path = runtime.evidence.path("runs", state.run_id, "checkpoint.json")
    data = json.loads(path.read_text())
    digest = data.pop("baseline")
    stored = json.loads(runtime.evidence.path("runs", state.run_id, f"baseline-{digest}.json").read_text())
    data.update(checkpoint_version=2, **stored)
    data["state"]["phase"] = "running"
    path.write_text(json.dumps(data))
    resumed = Runtime(tmp_path, Settings(approval="auto"))
    resumed.resume(state.run_id)
    assert resumed._initial_text == {"facts.txt": "original\n"}


def test_trace_tail_cache_keeps_the_hash_chain_and_notices_other_writers(tmp_path):
    from aster.safety.redaction import Redactor
    from aster.storage.evidence import EvidenceStore

    first = EvidenceStore(tmp_path, Redactor())
    other = EvidenceStore(tmp_path, Redactor())
    for n in range(3):
        first.emit("run", "tick", n=n)
    other.emit("run", "external")  # a second writer appends behind the first one's cache
    first.emit("run", "tick", n=3)
    rows = first.events("run")
    assert [row["seq"] for row in rows] == [1, 2, 3, 4, 5]
    assert all(b["previous"] == a["hash"] for a, b in zip(rows, rows[1:]))


@pytest.mark.asyncio
async def test_phase_timings_are_written_outside_run_state(tmp_path):
    (tmp_path / "a.txt").write_text("alpha\n")
    runtime = Runtime(tmp_path, Settings(approval="auto", memory_enabled=False))
    state = runtime.start(TaskSpec(prompt="inspect a.txt"))
    await runtime.step(ToolCall(name="read_file", arguments={"path": "a.txt"}))
    timings = json.loads(runtime.evidence.path("runs", state.run_id, "timings.json").read_text())
    assert timings["tool:read_file"][1] == 1 and timings["checkpoint"][1] >= 1
    assert "timings" not in state.model_dump()
