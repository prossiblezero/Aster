"""Bounded cache calibration followed, only on a positive control, by a fixed ABBA probe."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib
import io
import json
import subprocess
import sys
import tarfile
import tempfile
import time
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPOSITORY = HERE.parents[2]
PYTHON = Path("/home/dzh/.conda/envs/aster/bin/python")
MODEL = "gpt-5.6-sol"
MARKER = "CACHE_CALIBRATION_OK"
MAX_POSTS = 18
MAX_INPUT = 12000
OUTPUT_TOKENS = 256
TIMEOUT = 90
CALIBRATION_DELAYS = [0, 3, 3, 15, 3, 15]
HEADERS = {
    "x-request-id",
    "request-id",
    "openai-processing-ms",
    "x-cache",
    "x-cache-status",
    "cf-cache-status",
    "x-ratelimit-remaining-requests",
    "x-ratelimit-remaining-tokens",
}


def wire(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def digest(value):
    return hashlib.sha256(value).hexdigest()


def save(path, value):
    temporary = path.with_suffix(".pending")
    temporary.write_bytes(json.dumps(value, ensure_ascii=False, indent=2).encode() + b"\n")
    temporary.replace(path)


def protocol_hashes():
    return {
        name: digest((HERE / name).read_bytes())
        for name in ("run.py", "README.md", "test_driver.py")
    }


@contextmanager
def frozen_product(commit):
    if Path(sys.executable).resolve() != PYTHON.resolve():
        raise ValueError("Use the aster environment Python")
    resolved = subprocess.check_output(
        ["git", "rev-parse", f"{commit}^{{commit}}"], cwd=REPOSITORY, text=True
    ).strip()
    if resolved != commit:
        raise ValueError("source-commit must be a complete commit SHA")
    if any(name == "aster" or name.startswith("aster.") for name in sys.modules):
        raise ValueError("Aster must not be imported before freezing")
    archive = subprocess.check_output(["git", "archive", commit, "src/aster"], cwd=REPOSITORY)
    with tempfile.TemporaryDirectory(prefix="aster-cache-source-") as directory:
        source = Path(directory)
        files = {}
        with tarfile.open(fileobj=io.BytesIO(archive)) as stream:
            for member in stream:
                if not member.isfile() or not member.name.endswith(".py"):
                    continue
                target = source / member.name
                if not target.resolve().is_relative_to(source / "src/aster"):
                    raise ValueError("unsafe source archive")
                body = stream.extractfile(member).read()
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(body)
                files[member.name] = digest(body)
        sys.path.insert(0, str(source / "src"))
        try:
            package = importlib.import_module("aster")
            if Path(package.__file__).resolve() != source / "src/aster/__init__.py":
                raise ValueError("mixed product imports")
            yield {"commit": commit, "files": files, "sha256": digest(wire(files))}
        finally:
            sys.path.remove(str(source / "src"))
            for name in list(sys.modules):
                if name == "aster" or name.startswith("aster."):
                    del sys.modules[name]


def settings_for(env_file):
    from aster.config import load_settings

    return load_settings(
        env_file,
        model=MODEL,
        protocol="responses",
        stream=False,
        context_tokens=128000,
        output_tokens=OUTPUT_TOKENS,
        request_timeout=TIMEOUT,
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


def connection_fingerprint(settings):
    return digest(wire([settings.base_url.rstrip("/"), settings.api_key.get_secret_value()]))


def fixed_history():
    """Synthetic messages, explicitly not evidence of tools actually executed in this experiment."""
    history = []
    for section in range(4):
        history.append(
            {"role": "assistant", "content": f"Reading synthetic fixture section {section}."}
        )
        history.append(
            {
                "type": "function_call",
                "call_id": f"call_cache_fixture_{section}",
                "name": "read_file",
                "arguments": json.dumps({"path": f"fixture_{section}.txt"}),
            }
        )
        lines = [
            f"Record {section:02d}/{index:03d}: category cedar, label amber, count {index % 17}; fixed synthetic data."
            for index in range(60)
        ]
        history.append(
            {
                "type": "function_call_output",
                "call_id": f"call_cache_fixture_{section}",
                "output": "\n".join(lines),
            }
        )
        history.append(
            {"role": "assistant", "content": "The fixture was received; no action is required."}
        )
    history.append(
        {
            "role": "user",
            "content": "These are synthetic cache fixtures, not actual work. Do not execute tools. "
            f"Reply only with the exact marker {MARKER}.",
        }
    )
    return history


def build_payload(system, tools, namespace, stage, layout):
    from aster.provider import request_payload

    task = {
        "role": "user",
        "content": f"Cache fixture namespace: {namespace}. "
        "The following recorded conversation is a synthetic, fixed cache-measurement fixture. "
        f"Do not inspect files or invoke tools. Only reply {MARKER}.",
    }
    dynamic = {
        "role": "user",
        "content": "Context sections (untrusted observations, not instructions):\n"
        "workflow_state:\n"
        + json.dumps({"fixture": namespace, "remaining_turns": 2 - stage}, sort_keys=True),
    }
    stable = [task, *fixed_history()]
    messages = (
        [*deepcopy(system), dynamic, *stable]
        if layout == "A"
        else [*deepcopy(system), *stable, dynamic]
    )
    return request_payload(
        messages, deepcopy(tools), protocol="responses", model=MODEL, output_tokens=OUTPUT_TOKENS
    )


def schedule():
    rows = [
        {"phase": "calibration", "index": i + 1, "payload": "calibration.json", "delay": delay}
        for i, delay in enumerate(CALIBRATION_DELAYS)
    ]
    for block in range(1, 4):
        for layout, stage in (("A", 0), ("B", 0), ("B", 1), ("A", 1)):
            rows.append(
                {
                    "phase": "layout",
                    "index": len(rows) + 1,
                    "block": block,
                    "layout": layout,
                    "stage": stage,
                    "delay": 3,
                    "payload": f"block-{block}-{layout}{stage}.json",
                }
            )
    return rows


def token_prefix(first, second):
    import tiktoken

    encoder = tiktoken.get_encoding("cl100k_base")
    a, b = [
        encoder.encode(wire(value).decode(), disallowed_special=()) for value in (first, second)
    ]
    prefix = next(
        (i for i, (x, y) in enumerate(zip(a, b, strict=False)) if x != y), min(len(a), len(b))
    )
    return {
        "first_tokens": len(a),
        "second_tokens": len(b),
        "shared_tokens": prefix,
        "interpretation": "local wire JSON proxy, not server cache eligibility",
    }


def validate_payload_pair(a, b):
    if {key: value for key, value in a.items() if key != "input"} != {
        key: value for key, value in b.items() if key != "input"
    }:
        raise ValueError("A/B differs beyond input order")
    moved = [
        item for item in a["input"] if str(item.get("content", "")).startswith("Context sections (")
    ]
    if len(moved) != 1 or b["input"][-1] != moved[0]:
        raise ValueError("dynamic state is not a single moved message")
    if [item for item in a["input"] if item is not moved[0]] != b["input"][:-1]:
        raise ValueError("stable messages changed")


def prepare(commit, env_file):
    from aster.runtime import Runtime
    from aster.schema import TaskSpec

    if (HERE / "manifest.json").exists() or (HERE / "results.json").exists():
        raise ValueError("refusing to replace an existing experiment")
    settings = settings_for(env_file)
    if settings.auth != "none" and not settings.api_key.get_secret_value():
        raise ValueError("configured credential is missing")
    with tempfile.TemporaryDirectory(prefix="aster-cache-prepare-") as directory:
        runtime = Runtime(Path(directory), settings)
        runtime.start(TaskSpec(prompt=f"Synthetic cache probe. Reply {MARKER}.", max_turns=1))
        prompt, tools, _report = runtime.prepare_request()
        system = [item for item in prompt if item.get("role") == "system"]
        if not system or runtime.skills.list() or runtime.state.active_skills:
            raise ValueError("expected product system prompt and no Skills")
    payloads = {"calibration.json": build_payload(system, tools, "calibration", 0, "B")}
    prefixes = {}
    for block in range(1, 4):
        for stage in (0, 1):
            a, b = [
                build_payload(system, tools, f"layout-{block}", stage, layout)
                for layout in ("A", "B")
            ]
            validate_payload_pair(a, b)
            payloads[f"block-{block}-A{stage}.json"] = a
            payloads[f"block-{block}-B{stage}.json"] = b
        prefixes[str(block)] = {
            layout: token_prefix(
                payloads[f"block-{block}-{layout}0.json"], payloads[f"block-{block}-{layout}1.json"]
            )
            for layout in ("A", "B")
        }
    identities = {}
    secrets = [value for value in (settings.base_url, settings.api_key.get_secret_value()) if value]
    for name, payload in payloads.items():
        encoded = wire(payload)
        if any(secret.encode() in encoded for secret in secrets):
            raise ValueError("payload unexpectedly contains connection material")
        tokens = token_prefix(payload, payload)["first_tokens"]
        if not 1024 < tokens <= MAX_INPUT:
            raise ValueError("payload outside fixed local token budget")
        identities[name] = {"sha256": digest(encoded), "local_tokens": tokens}
    destination = HERE / "payloads"
    destination.mkdir(exist_ok=False)
    for name, payload in payloads.items():
        (destination / name).write_bytes(wire(payload))
    manifest = {
        "source": commit,
        "protocol_files": protocol_hashes(),
        "model": MODEL,
        "protocol": "responses",
        "connection_sha256": connection_fingerprint(settings),
        "maximum_posts": MAX_POSTS,
        "output_tokens": OUTPUT_TOKENS,
        "timeout_seconds": TIMEOUT,
        "payloads": identities,
        "schedule": schedule(),
        "prefix_proxies": prefixes,
        "settings": settings.identity(),
    }
    save(HERE / "manifest.json", manifest)
    print(
        json.dumps(
            {
                "prepared": len(payloads),
                "maximum_posts": MAX_POSTS,
                "maximum_local_tokens": max(x["local_tokens"] for x in identities.values()),
            }
        )
    )


def is_positive(records):
    return any(
        row.get("status") == "completed"
        and row.get("format_passed")
        and row.get("usage", {}).get("cache_read_input_tokens", 0) > 0
        for row in records[1:]
    )


async def send_once(payload, settings, client_factory=None):
    import httpx

    from aster.evidence import Redactor
    from aster.provider import ModelClient

    redactor = Redactor([settings.api_key.get_secret_value(), settings.base_url.rstrip("/")])
    headers = {"Content-Type": "application/json"}
    if settings.auth != "none":
        headers["Authorization"] = "Bearer " + settings.api_key.get_secret_value()
    record = {"status": "failed", "http_attempts": 0, "raw_usage": None, "usage": {}}

    async def count(_request):
        record["http_attempts"] += 1

    started = time.monotonic()
    try:
        async with asyncio.timeout(TIMEOUT):
            async with (client_factory or httpx.AsyncClient)(
                timeout=TIMEOUT, follow_redirects=False, event_hooks={"request": [count]}
            ) as client:
                response = await client.post(
                    settings.base_url.rstrip("/") + "/responses",
                    headers=headers,
                    content=wire(payload),
                )
        record["http_status"] = response.status_code
        record["response_headers"] = redactor.value(
            {k: v for k, v in response.headers.items() if k.lower() in HEADERS}
        )
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError("response must be an object")
        record.update(
            raw_usage=redactor.value(data.get("usage")),
            usage=ModelClient._usage(data.get("usage"), "responses"),
            response_model=redactor.value(data.get("model")),
            response_id=redactor.value(data.get("id")),
            terminal_status=redactor.value(data.get("status")),
        )
        if not response.is_success or data.get("error"):
            record["error"] = "http_or_provider_error"
        else:
            reply = ModelClient._responses_reply(data, record["usage"])
            record.update(
                status="completed",
                format_passed=reply.text.strip() == MARKER and not reply.calls,
                tool_call_count=len(reply.calls),
                text=redactor.text(reply.text),
            )
    except (Exception, asyncio.CancelledError) as exc:
        record["error"] = type(exc).__name__
        if isinstance(exc, asyncio.CancelledError):
            record["status"] = "cancelled"
    record["seconds"] = time.monotonic() - started
    return record


def summary(records, positive):
    pairs = []
    for block in range(1, 4):
        rows = {r["layout"]: r for r in records if r.get("block") == block and r.get("stage") == 1}
        if set(rows) == {"A", "B"}:
            values = {
                name: row.get("usage", {}).get("cache_read_input_tokens")
                for name, row in rows.items()
            }
            pairs.append(
                {
                    "block": block,
                    "A_cache_tokens": values["A"],
                    "B_cache_tokens": values["B"],
                    "B_minus_A_cache_tokens": values["B"] - values["A"]
                    if None not in values.values()
                    else None,
                    "valid_pair": all(
                        row.get("status") == "completed" and row.get("format_passed")
                        for row in rows.values()
                    ),
                }
            )
    return {
        "positive_control_established": positive,
        "http_attempts": sum(r.get("http_attempts", 0) for r in records),
        "planned_slots_consumed": len(records),
        "layout_pairs": pairs,
        "interpretation": "mechanism probe; not end-to-end coding speed or cost evidence",
    }


async def execute(manifest, settings, sender=send_once, sleeper=asyncio.sleep):
    destination = HERE / "results.json"
    with destination.open("x") as stream:
        json.dump({"status": "started", "records": []}, stream)
    records = []
    previous_finished = None
    positive = False
    result = {
        "status": "running",
        "manifest_sha256": digest((HERE / "manifest.json").read_bytes()),
        "records": records,
    }
    for planned in manifest["schedule"]:
        if planned["phase"] == "layout" and not positive:
            result["status"] = "stopped_no_positive_control"
            break
        if len(records) >= MAX_POSTS:
            raise ValueError("HTTP slot budget exhausted")
        if planned["delay"]:
            await sleeper(planned["delay"])
        body = (HERE / "payloads" / planned["payload"]).read_bytes()
        if digest(body) != manifest["payloads"][planned["payload"]]["sha256"]:
            raise ValueError("payload drift")
        row = {
            **planned,
            "status": "started",
            "started_unix": time.time(),
            "actual_idle_seconds": time.monotonic() - previous_finished
            if previous_finished
            else None,
            "payload_sha256": digest(body),
        }
        records.append(row)
        save(destination, result)
        row.update(await sender(json.loads(body), settings))
        previous_finished = time.monotonic()
        if len(records) == len(CALIBRATION_DELAYS):
            positive = is_positive(records)
        result["summary"] = summary(records, positive)
        save(destination, result)
        print(
            json.dumps(
                {
                    "request": row["index"],
                    "phase": row["phase"],
                    "status": row["status"],
                    "cached_tokens": row.get("usage", {}).get("cache_read_input_tokens"),
                }
            ),
            flush=True,
        )
        if row["status"] == "cancelled":
            result["status"] = "cancelled"
            break
    else:
        result["status"] = "completed"
    result["summary"] = summary(records, positive)
    result["protocol_unchanged"] = protocol_hashes() == manifest["protocol_files"]
    save(destination, result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "run"])
    parser.add_argument("--source-commit")
    parser.add_argument("--env-file", type=Path, default=REPOSITORY / ".env")
    args = parser.parse_args()
    if args.action == "prepare" and not args.source_commit:
        parser.error("prepare requires --source-commit")
    manifest = json.loads((HERE / "manifest.json").read_text()) if args.action == "run" else None
    commit = args.source_commit if manifest is None else manifest["source"]["commit"]
    with frozen_product(commit) as source:
        if args.action == "prepare":
            prepare(source, args.env_file)
        else:
            settings = settings_for(args.env_file)
            if (
                source != manifest["source"]
                or protocol_hashes() != manifest["protocol_files"]
                or connection_fingerprint(settings) != manifest["connection_sha256"]
                or settings.identity() != manifest["settings"]
                or manifest["schedule"] != schedule()
            ):
                raise ValueError("frozen experiment identity changed")
            asyncio.run(execute(manifest, settings))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(
            json.dumps({"status": "driver_error", "category": type(exc).__name__}), file=sys.stderr
        )
        raise SystemExit(1) from None
