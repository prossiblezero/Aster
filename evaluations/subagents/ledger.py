"""Real GPT ablations on frozen Aster, with whole-tree accounting and external grading."""

# ruff: noqa: E402
from __future__ import annotations

import hashlib
import json
import sys
import time
from collections import Counter
from contextlib import contextmanager
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "scripts"))

from aster.config import load_settings
from aster.core.hooks import HookResult
from aster.providers.client import ModelClient
from aster.providers.errors import ProviderError
from aster.storage.workspace import Workspace

LIMITS = {"model_calls": 40, "admitted_tools": 60, "reported_tokens": 500000, "seconds": 600}

def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")

def hashes(root, pattern="**/*"):
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.glob(pattern))
        if p.is_file()
        and not p.is_symlink()
        and "__pycache__" not in p.parts
        and ".pytest_cache" not in p.parts
    }

def product_hashes(root):
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in Workspace(root).files()
    }

def config(**overrides):
    settings = load_settings(
        REPO / ".env",
        context_tokens=64000,
        output_tokens=8192,
        stream=False,
        request_timeout=120,
        approval="auto",
        sandbox="required",
        network=False,
        read_parallelism=1,
        completion="strict",
        **overrides,
    )
    settings.stream = settings.protocol == "responses"
    return settings

class Ledger:
    """Evaluation-only ceilings, shared by the real parent and its real Workers."""

    def __init__(self, name, output):
        self.name, self.output = name, output
        self.requests = []
        self.tools = 0

    def before_tool(self, state, call):
        if self.tools >= LIMITS["admitted_tools"]:
            return HookResult("stop", "evaluation_whole_tree_tool_budget")
        self.tools += 1
        return HookResult()

    def after_tool(self, state, call, outcome):
        print(
            json.dumps(
                {
                    "name": self.name,
                    "run": state.run_id[:8],
                    "tool": call.name,
                    "status": outcome.status,
                    "child": bool(state.parent_run_id),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )

    @contextmanager
    def observe(self):
        original = ModelClient.complete
        ledger = self

        async def complete(client, messages, tools, *, on_event=None):
            if (
                len(ledger.requests) >= LIMITS["model_calls"]
                or sum(r.get("usage", {}).get("total_tokens", 0) for r in ledger.requests)
                >= LIMITS["reported_tokens"]
            ):
                raise ProviderError(
                    "evaluation_whole_tree_model_budget", category="evaluation_budget"
                )
            started = time.monotonic()
            # Persist wall time across reboots; measure latency with a monotonic clock.
            row = {"started_seconds": time.time(), "status": "pending", "attempts": []}
            ledger.requests.append(row)

            def event(e):
                if e.get("type") == "diagnostic":
                    row["attempts"].append(e)
                if on_event:
                    on_event(e)

            try:
                reply = await original(client, messages, tools, on_event=event)
                row.update(status="completed", response_model=reply.provider_model)
                return reply
            except BaseException as exc:
                row.update(status="failed", error_type=type(exc).__name__)
                raise
            finally:
                row.update(
                    seconds=time.monotonic() - started, usage=dict(client.last_usage)
                )
                save(ledger.output / "request-ledger.json", ledger.requests)

        ModelClient.complete = complete
        try:
            yield
        finally:
            ModelClient.complete = original

def collect(runtime, excluded):
    root = runtime.workspace.root
    runs = []
    tools = []
    memories = []
    total = Counter()
    for path in sorted((root / ".aster/runs").glob("*/checkpoint.json")):
        if path.parent.name in excluded:
            continue
        state = json.loads(path.read_text())["state"]
        events = runtime.evidence.events(state["run_id"])
        results = [e for e in events if e["event"] == "tool_result"]
        total.update(state["usage"])
        for e in results:
            tools.append(
                {
                    "run_id": state["run_id"],
                    "child": bool(state.get("parent_run_id")),
                    "name": e["call"]["name"],
                    "arguments": e["call"]["arguments"],
                    "status": e["outcome"]["status"],
                    "reason": e["outcome"]["reason"],
                    "changed_paths": e["outcome"]["changed_paths"],
                    "seq": e["seq"],
                }
            )
        memories.extend(
            {
                "run_id": state["run_id"],
                "seq": e["seq"],
                **{
                    k: v
                    for k, v in e.items()
                    if k in {"ids", "count", "status", "checked", "deactivated", "reason", "event"}
                },
            }
            for e in events
            if e["event"] in {"memory_recalled", "memory_dream"}
        )
        runs.append(
            {
                k: state.get(k)
                for k in [
                    "run_id",
                    "parent_run_id",
                    "phase",
                    "stop_reason",
                    "step",
                    "turn",
                    "usage",
                    "changed_paths",
                    "verification",
                ]
            }
        )
    return runs, tools, memories, dict(total)
