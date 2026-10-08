"""Source snapshots and identities shared by the subagent benchmark."""
from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def hashes(root):
    return {str(p.relative_to(root)): digest(p) for p in sorted(root.rglob("*.py"))}

def base(source):
    sys.path.insert(0, str(source))
    import aster
    if Path(aster.__file__).resolve() != source / "aster/__init__.py":
        raise ValueError("wrong_source_import")
    spec = importlib.util.spec_from_file_location(
        "light_evaluation", HERE / "support.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def harness_hashes():
    paths = [*HERE.glob("*.py"), HERE / "PROTOCOL.md", REPO / "scripts/benchmark_support.py",
             *sorted((REPO / "datasets/subagent-light-20260930").glob("*.json"))]
    return {str(p.relative_to(REPO)): digest(p) for p in paths}
