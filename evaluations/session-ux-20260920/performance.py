"""Synthetic history scale, local-only reconstruction and real Runtime checkout."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import statistics
import time
from functools import partial
from pathlib import Path

import aster
from aster.config import Settings
from aster.core.runtime import Runtime
from aster.schema import TaskSpec


def sample(call, repeats=30):
    call()  # warm cache
    values = []
    for _ in range(repeats):
        start = time.perf_counter_ns()
        call()
        values.append((time.perf_counter_ns() - start) / 1e6)
    ordered = sorted(values)
    return {"n": repeats, "p50_ms": statistics.median(values), "p95_ms": ordered[int(.95 * (len(values) - 1))], "samples_ms": values}


def main(output):
    output.mkdir(parents=True, exist_ok=False)
    rows = []
    for count in (1000, 10000, 100000):
        runtime = Runtime(output / str(count), Settings(completion="off"))
        runtime.start(TaskSpec(prompt="synthetic history scale"))
        empty = runtime.session_tree.cursor
        runtime._structured_history = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"entry-{i:06d} " + "x" * 128} for i in range(count)]
        runtime.checkpoint()
        full = runtime.session_tree.cursor
        tree = runtime.session_tree
        rebuilt = tree.restore(full)
        assert len(rebuilt["structured_history"]) == count
        assert tree.restore(empty)["structured_history"] == []
        rows.append({"message_count": count, "entry_count": len(tree.entries(runtime.state.session_id)),
                     "restore_full_path": sample(partial(tree.restore, full)),
                     "restore_short_ancestor": sample(partial(tree.restore, empty)),
                     "list_entries": sample(partial(tree.entries, runtime.state.session_id)),
                     "database_bytes": runtime.evidence.path("sessions.sqlite3").stat().st_size})
        runtime.sandbox.close()
    runtime = Runtime(output / "checkout", Settings(completion="off"))
    runtime.start(TaskSpec(prompt="real local checkpoint"))
    runtime.finish("fixture")
    entry = runtime.session_tree.cursor
    checkout = sample(lambda: runtime.checkout(entry), repeats=30)
    report = {"measurement": "local warm-cache synthetic history; no API or human time", "python": os.sys.executable,
              "source_import": aster.__file__, "platform": platform.platform(), "cpu_count": os.cpu_count(),
              "driver_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "history": rows, "runtime_checkout": checkout}
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"history": [{k: v for k, v in row.items() if k.endswith("count") or k.endswith("bytes")} for row in rows], "checkout_p95_ms": checkout["p95_ms"]}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    main(parser.parse_args().output.resolve())
