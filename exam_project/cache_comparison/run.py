"""Six single-POST cache probes using requests prepared by two frozen Aster revisions."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib
import io
import json
import os
import subprocess
import sys
import tarfile
import time
from copy import deepcopy
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPOSITORY = HERE.parents[1]
PYTHON = Path("/home/dzh/.conda/envs/aster/bin/python")
OLD_COMMIT = "59ca274f69665100dc20e3ef7343d009a84e3c35"
REQUESTS = 3
REQUEST_SECONDS = 300
EXPERIMENT_SECONDS = 1800
MARKER = "CACHE_PROBE_OK"
USAGE_FIELDS = (
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
    "reasoning_tokens",
)


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


def sha256(value):
    return hashlib.sha256(value).hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".pending")
    temporary.write_bytes(encoded(value) + b"\n")
    temporary.replace(path)


def source_snapshot(source):
    files = {}
    for path in sorted((source / "src/aster").rglob("*.py")):
        if path.is_symlink() or not path.resolve().is_relative_to(source / "src/aster"):
            raise ValueError("frozen source contains redirected Python files")
        files[str(path.relative_to(source))] = sha256(path.read_bytes())
    if "src/aster/runtime.py" not in files:
        raise ValueError("frozen source lacks src/aster/runtime.py")
    return {"sha256": sha256(encoded(files)), "files": files}


def frozen_imports(source, commit):
    if Path(sys.executable).resolve() != PYTHON.resolve():
        raise ValueError("Use /home/dzh/.conda/envs/aster/bin/python")
    revision = subprocess.check_output(
        ["git", "rev-parse", f"{commit}^{{commit}}"], cwd=REPOSITORY, text=True
    ).strip()
    if revision != commit:
        raise ValueError("source-commit must be a complete commit SHA")
    archive = subprocess.check_output(["git", "archive", commit, "src/aster"], cwd=REPOSITORY)
    with tarfile.open(fileobj=io.BytesIO(archive)) as stream:
        expected = {
            member.name: sha256(stream.extractfile(member).read())
            for member in stream
            if member.isfile() and member.name.endswith(".py")
        }
    snapshot = source_snapshot(source)
    if snapshot["files"] != expected:
        raise ValueError("frozen source does not exactly match the specified Git commit")
    configured = {
        Path(item).resolve() for item in os.environ.get("PYTHONPATH", "").split(os.pathsep) if item
    }
    if source / "src" not in configured:
        raise ValueError("Set PYTHONPATH to frozen src before starting Python")
    package = importlib.import_module("aster")
    if Path(package.__file__).resolve() != source / "src/aster/__init__.py":
        raise ValueError("aster was imported from another source tree")
    for name, module in list(sys.modules.items()):
        if name.startswith("aster.") and getattr(module, "__file__", None):
            if not Path(module.__file__).resolve().is_relative_to(source / "src/aster"):
                raise ValueError("mixed Aster imports")
    return snapshot


def experiment_snapshot():
    return {
        name: sha256((HERE / name).read_bytes())
        for name in ("run.py", "task.txt", "test_driver.py", "README.md")
    }


def settings_for(env_file):
    from aster.config import load_settings

    return load_settings(
        env_file,
        model="gpt-5.6-sol",
        protocol="responses",
        stream=False,
        context_tokens=128000,
        output_tokens=32768,
        request_timeout=REQUEST_SECONDS,
        sandbox="required",
        approval="never",
        read_only=True,
        network=False,
        memory_enabled=False,
        memory_evolution=False,
        auto_dream=False,
        max_workers=0,
        read_parallelism=1,
    )


def connection_hash(settings):
    return sha256(encoded([settings.base_url.rstrip("/"), settings.api_key.get_secret_value()]))


def control_record(prompt, tools):
    """Compare content after allowing only the product's layout/mode/catalog migration."""
    messages = deepcopy(prompt)
    dynamic = [
        item for item in messages if item.get("content", "").startswith("Context sections (")
    ]
    if len(dynamic) != 1 or len(messages) != 3:
        raise ValueError("probe must have only system, initial task and dynamic context")
    messages.remove(dynamic[0])
    workflow = json.loads(dynamic[0]["content"].split("workflow_state:\n", 1)[1])
    mode = workflow.pop("mode", "work")
    messages[0]["content"] = messages[0]["content"].replace("\nCurrent runtime mode: work.", "")
    schemas = deepcopy(tools)
    for tool in schemas:
        if tool["name"] == "tool_load":
            names = tool["parameters"]["properties"]["names"]["items"]["enum"]
            tool["parameters"]["properties"]["names"]["items"]["enum"] = [
                name for name in names if name != "observation_read"
            ]
    return {"messages": messages, "workflow": workflow, "mode": mode, "tools": schemas}


def prefix_metrics(payloads):
    import tiktoken

    encoder = tiktoken.get_encoding("cl100k_base")
    tokens = [
        encoder.encode(encoded(payload).decode(), disallowed_special=()) for payload in payloads
    ]

    def shared(rows):
        return next(
            (index for index, group in enumerate(zip(*rows, strict=False)) if len(set(group)) != 1),
            min(map(len, rows)),
        )

    return {
        "proxy": "cl100k_base over exact UTF-8 wire JSON; not server tokenization or cache eligibility",
        "payload_tokens": list(map(len, tokens)),
        "all_three_shared_tokens": shared(tokens),
        "adjacent_shared_tokens": [shared(tokens[index : index + 2]) for index in range(2)],
    }


def prepare(args, snapshot):
    from aster.provider import request_payload
    from aster.runtime import Runtime
    from aster.schema import TaskSpec

    if args.condition == "old" and args.source_commit != OLD_COMMIT:
        raise ValueError("old condition must use the pre-layout commit")
    destination = HERE / args.condition
    if destination.exists():
        raise ValueError("Refusing to overwrite a prepared condition")
    settings = settings_for(args.env_file)
    if settings.auth != "none" and not settings.api_key.get_secret_value():
        raise ValueError("provider credential missing")
    root = destination / "workspace"
    root.mkdir(parents=True)
    runtime = Runtime(root, settings)
    task = (HERE / "task.txt").read_text()
    if runtime.context.count(task) <= 1024:
        raise ValueError("stable task must exceed 1024 local proxy tokens")
    runtime.start(TaskSpec(prompt=task, max_steps=1, max_turns=REQUESTS))
    if runtime.skills.list() or runtime.state.active_skills:
        raise ValueError("Skills must be absent and inactive")
    records, payloads = [], []
    for index in range(REQUESTS):
        runtime.state.turn = index
        prompt, tools, report = runtime.prepare_request()
        payload = request_payload(
            prompt,
            tools,
            protocol=settings.protocol,
            model=settings.model,
            output_tokens=settings.effective_output_tokens,
        )
        if report.compacted or runtime._structured_history or runtime.state.messages:
            raise ValueError("probe must have no compaction or generated history")
        payloads.append(payload)
        records.append(
            {
                "index": index + 1,
                "payload_sha256": sha256(encoded(payload)),
                "context_tokens": report.estimated_tokens,
                "policy_version": report.policy_version,
                "control": control_record(prompt, tools),
            }
        )
        write_json(destination / "payloads" / f"{index + 1}.json", payload)
    manifest = {
        "condition": args.condition,
        "source_commit": args.source_commit,
        "source_path": str(args.source_path),
        "source": snapshot,
        "configuration": settings.identity(),
        "connection_sha256": connection_hash(settings),
        "experiment_files": experiment_snapshot(),
        "requests": records,
        "task_sha256": sha256(task.encode()),
        "task_proxy_tokens": runtime.context.count(task),
        "prefix": prefix_metrics(payloads),
        "skills": "empty catalog; no activation or calls",
        "history": "empty on every request; outputs are never fed back",
        "request_limit": REQUESTS,
        "http_attempt_limit": REQUESTS,
        "request_seconds": REQUEST_SECONDS,
        "experiment_seconds": EXPERIMENT_SECONDS,
    }
    if source_snapshot(args.source_path) != snapshot:
        raise ValueError("source changed during preparation")
    write_json(destination / "manifest.json", manifest)
    print(
        json.dumps(
            {"condition": args.condition, "prepared": REQUESTS, "prefix": manifest["prefix"]}
        )
    )


def validate_pair():
    manifests = [json.loads((HERE / name / "manifest.json").read_text()) for name in ("old", "new")]
    old, new = manifests
    for key in ("configuration", "connection_sha256", "experiment_files", "task_sha256"):
        if old[key] != new[key]:
            raise ValueError(f"conditions differ beyond layout/catalog: {key}")
    if [item["control"] for item in old["requests"]] != [
        item["control"] for item in new["requests"]
    ]:
        raise ValueError("request contents differ beyond the approved layout/catalog migration")
    for manifest in manifests:
        controls = deepcopy([item["control"] for item in manifest["requests"]])
        for index, control in enumerate(controls):
            if control["workflow"].pop("remaining_turns") != REQUESTS - index:
                raise ValueError("unexpected dynamic state")
        if any(control != controls[0] for control in controls[1:]):
            raise ValueError("only remaining_turns may change within a condition")
    return manifests


async def send_once(payload, settings):
    """One raw POST deliberately bypasses ModelClient's three-attempt retry loop."""
    import httpx

    from aster.evidence import Redactor
    from aster.provider import ModelClient, ProviderError

    redactor = Redactor([settings.api_key.get_secret_value()])
    headers = {"Content-Type": "application/json"}
    if settings.auth != "none":
        secret = settings.api_key.get_secret_value()
        if not secret:
            raise ValueError("provider credential missing")
        headers["Authorization"] = f"Bearer {secret}"
    result = {"status": "failed", "http_attempts": 0, "usage": {}, "raw_usage": None}

    async def count_request(_request):
        result["http_attempts"] += 1

    started = time.monotonic()
    try:
        async with asyncio.timeout(REQUEST_SECONDS):
            async with httpx.AsyncClient(
                timeout=REQUEST_SECONDS,
                follow_redirects=False,
                event_hooks={"request": [count_request]},
            ) as client:
                response = await client.post(
                    settings.base_url.rstrip("/") + "/responses",
                    headers=headers,
                    content=encoded(payload),
                )
        result["http_status"] = response.status_code
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError("provider returned non-object JSON")
        result["raw_usage"] = redactor.value(data.get("usage"))
        result["usage"] = ModelClient._usage(data.get("usage"), "responses")
        if not response.is_success or data.get("error"):
            result["error"] = "http_or_provider_error"
        else:
            reply = ModelClient._responses_reply(data, result["usage"])
            result.update(
                status="completed",
                text=redactor.text(reply.text),
                tool_call_count=len(reply.calls),
                format_passed=reply.text.strip() == MARKER and not reply.calls,
            )
    except (Exception, asyncio.CancelledError) as exc:
        result["error"] = type(exc).__name__
        if isinstance(exc, asyncio.CancelledError):
            result["status"] = "cancelled"
        if isinstance(exc, ProviderError):
            result["category"] = exc.details["category"]
    result["seconds"] = time.monotonic() - started
    return result


def summarize(records):
    usage = {}
    for name in USAGE_FIELDS:
        values = [row["usage"][name] for row in records if name in row.get("usage", {})]
        usage[name] = {
            "reported_sum": sum(values) if values else None,
            "reported_requests": len(values),
            "planned_requests": REQUESTS,
            "complete": len(values) == REQUESTS,
        }
    return {
        "completed": sum(row.get("status") == "completed" for row in records),
        "format_passed": sum(row.get("format_passed", False) for row in records),
        "seconds": sum(row.get("seconds", 0) for row in records),
        "usage": usage,
    }


async def run(args, snapshot):
    manifests = validate_pair()
    manifest = manifests[args.condition == "new"]
    settings = settings_for(args.env_file)
    if (
        manifest["source_commit"] != args.source_commit
        or manifest["source"] != snapshot
        or manifest["configuration"] != settings.identity()
        or manifest["connection_sha256"] != connection_hash(settings)
        or manifest["experiment_files"] != experiment_snapshot()
    ):
        raise ValueError("prepared source, settings or experiment drifted")
    result_path = HERE / args.condition / "result.json"
    if result_path.exists():
        raise ValueError("Refusing an additional attempt or overwrite")
    experiment_path = HERE / "experiment.json"
    manifest_hashes = {
        name: sha256((HERE / name / "manifest.json").read_bytes()) for name in ("old", "new")
    }
    if args.condition == "old":
        with experiment_path.open("x") as stream:
            json.dump(
                {
                    "started_unix": time.time(),
                    "order": ["old"] * 3 + ["new"] * 3,
                    "manifest_sha256": manifest_hashes,
                },
                stream,
            )
    elif not (HERE / "old/result.json").exists():
        raise ValueError("old must run before new")
    experiment = json.loads(experiment_path.read_text())
    if manifest_hashes != experiment["manifest_sha256"]:
        raise ValueError("condition manifests changed after the first request")
    started = experiment["started_unix"]
    results = {"condition": args.condition, "source_commit": args.source_commit, "requests": []}
    write_json(result_path, results)
    for prepared in manifest["requests"]:
        if time.time() - started >= EXPERIMENT_SECONDS:
            results["stop_reason"] = "experiment_wall_limit"
            break
        payload = json.loads(
            (HERE / args.condition / "payloads" / f"{prepared['index']}.json").read_text()
        )
        if sha256(encoded(payload)) != prepared["payload_sha256"]:
            raise ValueError("prepared payload drifted")
        record = {
            "index": prepared["index"],
            "payload_sha256": prepared["payload_sha256"],
            "status": "started",
        }
        results["requests"].append(record)
        write_json(
            result_path, results
        )  # A crash still consumes this request; reruns are forbidden.
        remaining = EXPERIMENT_SECONDS - (time.time() - started)
        try:
            async with asyncio.timeout(remaining):
                record.update(await send_once(payload, settings))
        except TimeoutError:
            record.update(status="failed", error="experiment_wall_limit")
        results["summary"] = summarize(results["requests"])
        write_json(result_path, results)
        print(json.dumps({"condition": args.condition, **record}), flush=True)
        if record["status"] == "cancelled":
            results["stop_reason"] = "cancelled_or_experiment_wall_limit"
            break
    results["source_unchanged"] = source_snapshot(args.source_path) == snapshot
    results["experiment_unchanged"] = experiment_snapshot() == manifest["experiment_files"]
    results["summary"] = summarize(results["requests"])
    write_json(result_path, results)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "run"))
    parser.add_argument("--condition", choices=("old", "new"), required=True)
    parser.add_argument("--source-path", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--env-file", type=Path, required=True)
    args = parser.parse_args()
    args.source_path = args.source_path.resolve()
    snapshot = frozen_imports(args.source_path, args.source_commit)
    if args.action == "prepare":
        prepare(args, snapshot)
    else:
        asyncio.run(run(args, snapshot))


if __name__ == "__main__":
    main()
