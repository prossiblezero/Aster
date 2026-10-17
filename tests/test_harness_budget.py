"""Harness bookkeeping stays proportional to what changed, not to repository size.

Counts are deterministic, so this guards the v12 overhead fixes in CI without timing noise:
before them, 16 scripted steps on this repository hashed 136 MB and called fsync 137 times."""

import asyncio
import hashlib
import importlib.util
import os
from pathlib import Path

import pytest

import aster.storage.workspace as workspace_module

pytestmark = pytest.mark.core

BENCH = Path(__file__).resolve().parents[1] / "scripts" / "bench_harness_overhead.py"


def test_scripted_run_hashes_and_syncs_within_budget(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("bench_harness_overhead", BENCH)
    bench = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bench)
    counts = {"hashed": 0, "fsync": 0}

    class CountingSha256:
        def __init__(self, data=b""):
            self.inner = hashlib.sha256()
            self.update(data)

        def update(self, data):
            counts["hashed"] += len(data)
            self.inner.update(data)

        def hexdigest(self):
            return self.inner.hexdigest()

    real_fsync = os.fsync

    def counting_fsync(fd):
        counts["fsync"] += 1
        return real_fsync(fd)

    # Files just written are inside the racy window and would always be rehashed.
    monkeypatch.setattr(workspace_module, "RACY_WINDOW_NS", 0, raising=False)
    monkeypatch.setattr(workspace_module, "hashlib", type("Hashlib", (), {"sha256": CountingSha256}))
    monkeypatch.setattr(os, "fsync", counting_fsync)
    bench.build_repo(tmp_path, 200, 4)
    size = sum(p.stat().st_size for p in tmp_path.rglob("*") if p.is_file())
    _, state = asyncio.run(bench.run(tmp_path, 16, "off"))
    assert state.phase == "completed" and state.turn == 17
    assert counts["hashed"] < 6 * size, counts  # about 4x today: initial snapshots only
    assert counts["fsync"] < 4 * state.turn, counts  # group commit; about 3 per turn today
