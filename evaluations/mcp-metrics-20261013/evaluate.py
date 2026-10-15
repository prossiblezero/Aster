"""Bounded MCP measurements using the real Runtime, model and configured services."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import statistics
import subprocess
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from aster.config import load_settings
from aster.context.budget import BudgetCalculator
from aster.core.runtime import Runtime
from aster.providers.client import ModelClient
from aster.schema import TaskSpec, ToolCall
from aster.tools.mcp import MCPParameters
from aster.tools.schemas import definitions

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
PRIVATE = REPO / "artifacts/private" / HERE.name
LIMITS = {"max_steps": 24, "max_turns": 12, "max_tokens": 160000}
COUNTER = BudgetCalculator()


def read(path):
    return json.loads(path.read_text())


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_suffix(path.suffix + ".tmp")
    staged.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    staged.replace(path)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def settings():
    return load_settings(
        REPO / ".env", REPO / "mcp.toml.example", approval="auto",
        memory_enabled=False, memory_evolution=False, auto_dream=False,
        context_tokens=65536, output_tokens=4096, stream=False,
        request_timeout=60, max_workers=0, completion="strict",
    )


def source_hashes():
    return {str(p.relative_to(REPO)): digest(p) for p in sorted((REPO / "src/aster").rglob("*.py"))}


def populate(runtime, catalog):
    """Use frozen metadata only for offline retrieval and schema-size measurements."""
    for name, row in catalog.items():
        runtime.mcp.tools[name] = (row["server"], row["original"])
        runtime.mcp.schemas[name] = (
            MCPParameters(row["schema"], write=row["effect"] == "write"),
            row["description"], row["effect"],
        )
        runtime.mcp.discovered.add(row["server"])
        runtime.mcp.sources[row["server"]] = row["server"]


def freeze():
    path = HERE / "freeze.json"
    assert not path.exists(), "Do not overwrite the experiment freeze"
    s = settings()
    save(path, {"created_utc": datetime.now(UTC).isoformat(),
                "head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip(),
                "source": source_hashes(), "settings": s.identity(), "limits": LIMITS,
                "seconds_per_run": 240, "repeats": 2, "concurrent_pairs": 2,
                "inputs": {p.name: digest(p) for p in [HERE / "evaluate.py", HERE / "cases.json",
                           HERE / "queries.json", HERE / "catalog.json", HERE / "full-github-catalog.json"]}})


def check_freeze():
    frozen = read(HERE / "freeze.json")
    assert frozen["source"] == source_hashes(), "Production source drift"
    assert frozen["settings"] == settings().identity(), "Settings drift"
    for name, expected in frozen["inputs"].items():
        assert digest(HERE / name) == expected, f"Frozen input changed: {name}"


class MeasuredModel(ModelClient):
    def __init__(self, config):
        super().__init__(config)
        self.requests = []

    async def complete(self, messages, tools, **kwargs):
        start = time.perf_counter()
        callback = kwargs.pop("on_event", None)
        attempts = []

        def observe(event):
            if event.get("type") == "diagnostic":
                attempts.append(event)
            if callback:
                callback(event)

        try:
            return await super().complete(messages, tools, on_event=observe, **kwargs)
        finally:
            self.requests.append({"seconds": time.perf_counter() - start,
                                  "schema_tokens_estimated": COUNTER.count(tools),
                                  "visible_tools": [t["name"] for t in tools],
                                  "usage": self.last_usage, "diagnostics": self.last_diagnostics,
                                  "attempts": attempts})


def answer_json(text):
    try:
        return json.JSONDecoder().raw_decode(text[text.index("{"):])[0]
    except (ValueError, json.JSONDecodeError):
        return None


def call_relevant(case, call):
    name, args = call["name"], call["arguments"]
    if name not in case["tools"]:
        return False
    if case["kind"] == "github":
        target = case["target"]
        if name.endswith("search_repositories"):
            query = args.get("query", "").lower()
            return target["repo"].lower() in query and target["owner"].lower() in query
        if any(args.get(k) != target[k] for k in ("owner", "repo")):
            return False
        if "issue_number" in target and args.get("issue_number") != target["issue_number"]:
            return False
        if name.endswith("get_commit") and args.get("sha") != target["sha"]:
            return False
        if name.endswith("get_file_contents"):
            return (args.get("path", "").lstrip("/") == "package.json"
                    and (args.get("sha") == target["sha"] or args.get("ref") == target["sha"]))
        return True
    path = args.get("path", ".").removeprefix("./")
    if name.endswith("write_file"):
        return path == case.get("product")
    if name.endswith("delete_file"):
        return path == case.get("deleted")
    if name.endswith("create_directory"):
        return path == "reports"
    if name.endswith("list_directory"):
        return path in {".", "input", "reports"}
    return path in {*case.get("files", {}), case.get("product")}


def grade(case, state, root, events):
    final = answer_json(state.final)
    checks = {"answer": isinstance(final, dict) and all(final.get(k) == v for k, v in case["gold"].items()),
              "completed": state.phase == "completed"}
    calls = [e for e in events if e["event"] == "tool_result"]
    successful = {e["call"]["name"] for e in calls if e["outcome"]["status"] == "success"}
    checks["required_mcp_tools"] = set(case.get("required_tools", case["tools"])) <= successful
    substitutes = {"read_file", "write_file", "patch_file", "patch_files", "list_files", "search", "run_shell", "run_tests"}
    checks["mcp_only"] = not any(e["call"]["name"] in substitutes for e in calls)
    if case.get("product"):
        try:
            checks["product"] = read(root / case["product"]) == case["gold"]
        except (OSError, ValueError):
            checks["product"] = False
    if case.get("deleted"):
        checks["deleted"] = not (root / case["deleted"]).exists()
    for name, content in case.get("files", {}).items():
        if name not in {case.get("product"), case.get("deleted")}:
            try:
                checks["preserved:" + name] = (root / name).read_text() == content
            except (OSError, UnicodeError):
                checks["preserved:" + name] = False
    return checks


async def run_one(case, arm, repeat):
    check_freeze()
    name = f"{case['id']}--{arm}--{repeat}"
    result_path = HERE / "results" / (name + ".json")
    if result_path.exists():
        return read(result_path)
    root = PRIVATE / "runs" / name
    assert not root.exists(), "Unfinished run needs separate audit, not overwrite"
    root.mkdir(parents=True)
    for path, content in case.get("files", {}).items():
        dest = root / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content)
    config = settings()
    model = MeasuredModel(config)
    runtime = Runtime(root, config, model=model)
    task = TaskSpec(prompt=case["prompt"], **LIMITS, require_observation=True,
                    require_edit=bool(case.get("product") or case.get("deleted")), require_verification=False)
    runtime.start(task)
    error, startup = "", 0
    start = time.perf_counter()
    try:
        async with asyncio.timeout(240):
            if arm == "eager":
                for server in config.mcp_servers:
                    await runtime.mcp.discover(server)
                if runtime.mcp.errors:
                    raise RuntimeError(str(runtime.mcp.errors))
                runtime.state.loaded_tools = sorted(runtime.mcp.tools)
                startup = time.perf_counter() - start
            await runtime.run()
    except Exception as exc:
        error = runtime.evidence.redactor.text(f"{type(exc).__name__}: {exc}")
    seconds = time.perf_counter() - start
    state = runtime.state
    events = runtime.evidence.events(state.run_id)
    checks = grade(case, state, root, events)
    tool_events = [e for e in events if e["event"] == "tool_result"]
    calls = [{"call": e["call"], "status": e["outcome"]["status"], "reason": e["outcome"]["reason"],
              "seconds": e["outcome"]["duration_ms"] / 1000} for e in tool_events]
    mcp = [e for e in calls if e["call"]["name"].startswith("mcp__")]
    known_usage = Counter()
    for request in model.requests:
        known_usage.update(request["usage"])
    missing_usage = sum("total_tokens" not in r["usage"] for r in model.requests)
    frozen_catalog = read(HERE / "catalog.json")
    schema_unchanged = all(runtime.mcp.schemas[n][0].schema == frozen_catalog[n]["schema"]
                           for n in runtime.mcp.tools)
    result = {"id": name, "case": case["id"], "kind": case["kind"], "arm": arm, "repeat": repeat,
              "freeze_sha256": digest(HERE / "freeze.json"), "passed": all(checks.values()),
              "checks": checks, "seconds": seconds, "startup_seconds": startup, "error": error,
              "phase": state.phase, "stop_reason": state.stop_reason, "final": state.final,
              "usage": dict(known_usage), "state_usage_matches": dict(known_usage) == state.usage,
              "requests_without_usage": missing_usage, "model_requests": len(model.requests),
              "mcp_calls": len(mcp), "mcp_successes": sum(e["status"] == "success" for e in mcp),
              "mcp_relevant_valid_calls": sum(call_relevant(case, e["call"]) and e["status"] == "success" for e in mcp),
              "mcp_invalid_arguments": sum(e["reason"] == "invalid_arguments" for e in mcp),
              "schema_unchanged": schema_unchanged, "requests": model.requests, "tools": calls,
              "trace_sha256": digest(root / ".aster/runs" / state.run_id / "trace.jsonl")}
    save(result_path, result)
    await runtime.workers.close()
    runtime.sandbox.close()
    print(json.dumps({k: result[k] for k in ("id", "passed", "seconds", "model_requests", "usage", "error")}), flush=True)
    return result


async def run_pairs():
    semaphore = asyncio.Semaphore(2)

    async def pair(index, case, repeat):
        async with semaphore:
            arms = ["eager", "lazy"] if (index + repeat) % 2 else ["lazy", "eager"]
            for arm in arms:
                row = await run_one(case, arm, repeat)
                if not row["model_requests"] or row["requests_without_usage"] == row["model_requests"]:
                    raise RuntimeError("Model unavailable; preserve the failed run and stop")

    cases = read(HERE / "cases.json")
    # Bound simultaneous paid work and counterbalance order within matched pairs.
    for offset in range(0, len(cases), 2):
        for repeat in (1, 2):
            await asyncio.gather(*(pair(i, cases[i], repeat) for i in range(offset, min(offset + 2, len(cases)))))


async def retrieval():
    r = Runtime(PRIVATE / "retrieval", settings())
    r.start(TaskSpec(prompt="Measure frozen-catalog retrieval"))
    catalog = read(HERE / "catalog.json")
    populate(r, catalog)
    rows = []
    for item in read(HERE / "queries.json"):
        start = time.perf_counter()
        result = await r.mcp.execute(ToolCall(name="mcp_list", arguments={
            "server": "", "query": item["query"], "offset": 0, "limit": 50, "refresh": False}))
        elapsed = time.perf_counter() - start
        names = [t["name"] for t in json.loads(result.content)["tools"]]
        gold = set(item["relevant"])
        rows.append({**item, "results": names, "seconds": elapsed,
                     **{f"recall_at_{k}": len(gold & set(names[:k])) / len(gold) for k in (1, 3, 5)},
                     "reciprocal_rank": next((1 / (i + 1) for i, n in enumerate(names) if n in gold), 0)})
    save(HERE / "retrieval.json", rows)
    full = read(HERE / "full-github-catalog.json")
    expanded = dict(catalog)
    for original, (schema, description) in full.items():
        expanded.setdefault("mcp__github__" + original, {"server": "github", "original": original,
                            "schema": schema, "description": "[github] " + description, "effect": "read"})
    sizes = sorted({len(catalog), min(30, len(expanded)), len(expanded)})
    measurements = []
    selected = ["mcp__filesystem__read_file", "mcp__filesystem__write_file"]
    for size in sizes:
        subset = dict(list(expanded.items())[:size])
        populate(r, subset)
        names = list(subset)
        base = sorted(set(r._tool_names()) - set(expanded))
        available = [n for n in r.mcp.schemas if not r._unavailable_reason(n)]
        eager = definitions(base + names, available=available, schemas=r.mcp.schemas)
        lazy = definitions(base + selected, available=available, schemas=r.mcp.schemas)
        measurements.append({"catalog_tools": size, "eager_schema_tokens": COUNTER.count(eager),
                             "two_loaded_schema_tokens": COUNTER.count(lazy),
                             "tokenizer": "cl100k_base", "kind": "offline_schema_only"})
    save(HERE / "schema-scaling.json", measurements)
    r.sandbox.close()
    print(json.dumps({"retrieval_queries": len(rows), "schema_scaling": measurements}), flush=True)


async def idempotency():
    rows = []
    for i in range(10):
        root = PRIVATE / "idempotency" / str(i)
        assert not root.exists()
        config = settings()
        config.mcp_servers = {"filesystem": config.mcp_servers["filesystem"]}
        runtime = Runtime(root, config)
        runtime.start(TaskSpec(prompt="Measure repeated writes", max_steps=20))
        args = {"path": "once.txt", "content": f"value {i}"}

        def request(arguments=None, original=args, key=f"operation-{i}"):
            return ToolCall(name="mcp__filesystem__write_file", arguments=arguments or original, idempotency_key=key)

        first = await runtime.step(request())
        assert first.status == "success", first.content
        before = (root / "once.txt").stat().st_mtime_ns
        retry = await runtime.step(request())
        restored = Runtime(root, config)
        restored.resume(runtime.state.run_id)
        replay = await restored.step(request())
        conflict = await restored.step(request({**args, "content": "different"}))
        unchanged = (root / "once.txt").read_text() == args["content"] and (root / "once.txt").stat().st_mtime_ns == before
        rows.append({"operation": i, "same_session_replay": retry.data.get("idempotency_replayed", False),
                     "restored_replay": replay.data.get("idempotency_replayed", False),
                     "conflict_blocked": conflict.reason == "idempotency_conflict", "file_unchanged": unchanged})
        runtime.sandbox.close()
        restored.sandbox.close()
    save(HERE / "idempotency.json", rows)
    print(json.dumps({"idempotency_operations": len(rows), "checks_passed": sum(all(v for k, v in row.items() if k != "operation") for row in rows)}), flush=True)


def summarize():
    rows = [read(p) for p in sorted((HERE / "results").glob("*.json"))]
    result = {"runs": len(rows), "arms": {}}
    for arm in ("eager", "lazy"):
        group = [r for r in rows if r["arm"] == arm]
        if not group:
            continue
        result["arms"][arm] = {
            "runs": len(group), "passed": sum(r["passed"] for r in group),
            **{k: sum(r[k] for r in group) for k in ("model_requests", "mcp_calls", "mcp_successes", "mcp_relevant_valid_calls", "mcp_invalid_arguments", "requests_without_usage")},
            "usage": dict(sum((Counter(r["usage"]) for r in group), Counter())),
            "mean_seconds": statistics.mean(r["seconds"] for r in group),
            "median_seconds": statistics.median(r["seconds"] for r in group),
            "mean_first_schema_tokens": statistics.mean(r["requests"][0]["schema_tokens_estimated"] for r in group if r["requests"]),
            "mean_first_input_tokens": statistics.mean(r["requests"][0]["usage"].get("input_tokens", 0) for r in group if r["requests"]),
        }
    save(HERE / "summary.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["freeze", "retrieval", "idempotency", "run", "summarize"])
    mode = parser.parse_args().mode
    if mode == "freeze":
        freeze()
    elif mode == "summarize":
        summarize()
    else:
        check_freeze()
        asyncio.run({"retrieval": retrieval, "idempotency": idempotency, "run": run_pairs}[mode]())
