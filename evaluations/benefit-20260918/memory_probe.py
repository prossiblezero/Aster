"""Prepare sourced Memory without a model; audit four separately launched read-only tasks."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from aster.config import Settings
from aster.runtime import Runtime
from aster.schema import TaskSpec, ToolCall

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
PYTHON = Path("/home/dzh/.conda/envs/aster/bin/python")
RULES = ["data-contract.md", "time-rules.md", "calendar-rules.md", "backup-rules.md"]
ORDER = [
    ("storage-backup", False),
    ("storage-backup", True),
    ("calendar-time", True),
    ("calendar-time", False),
]
QUERIES = [
    ("浏览器保存课业清单时，使用哪个存储键和数据版本？", "data-contract.md"),
    ("输入的本地日历时间要按什么时区解释？", "time-rules.md"),
    ("同一任务反复导出时，日程标识怎样保持不变？", "calendar-rules.md"),
    ("备份导入失败会留下部分记录吗？", "backup-rules.md"),
]
ANSWERS = {
    "storage-backup": {
        "storage_key": "campus-planner-v1",
        "version": 1,
        "backup_filename": "campus-planner-backup.json",
        "merge_by": "id",
        "same_id": "replace",
        "absent_existing": "keep",
        "duplicate_import_ids": "reject_all",
        "invalid_import": "no_change",
    },
    "calendar-time": {
        "timezone": "Asia/Shanghai",
        "utc_offset": "+08:00",
        "stored_due": "local_string",
        "uid_suffix": "@campus-planner.local",
        "repeated_uid": "same",
        "duration_minutes": 30,
        "completed_tasks": "exclude",
        "dtstart": "20261008T161000Z",
        "dtend": "20261008T164000Z",
    },
}
SOURCES = {
    "storage-backup": {"docs/data-contract.md", "docs/backup-rules.md"},
    "calendar-time": {"docs/time-rules.md", "docs/calendar-rules.md"},
}


def write_once(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as stream:
        stream.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def snapshot(root, *, include_state=False):
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
        and not any(
            part in {".git", "__pycache__", ".pytest_cache"}
            or (part == ".aster" and not include_state)
            for part in path.relative_to(root).parts
        )
    }


class OfflineModel:
    async def complete(self, *_args, **_kwargs):
        raise AssertionError("Memory preparation and diagnostics must not call a model")


def offline_runtime(root):
    return Runtime(
        root,
        Settings(
            approval="auto",
            read_only=True,
            memory_enabled=True,
            memory_evolution=False,
            auto_dream=False,
            max_workers=0,
            completion="off",
        ),
        model=OfflineModel(),
    )


def copy_product(source, target):
    shutil.copytree(
        source,
        target,
        ignore=shutil.ignore_patterns(
            ".aster", "__pycache__", ".pytest_cache", ".git", "node_modules", "*.pyc"
        ),
    )


async def prepare(seed, output, source_commit):
    if output.exists():
        raise ValueError("Refusing to overwrite existing Memory preparation")
    if not (seed / "index.html").is_file():
        raise ValueError("A validated product snapshot is required")
    if snapshot(seed / "docs") != snapshot(HERE / "rules"):
        raise ValueError("Product docs differ from the frozen public rules")
    output.mkdir(parents=True)
    root = output / "seed"
    copy_product(seed, root)
    before = snapshot(root)
    runtime = offline_runtime(root)
    runtime.start(
        TaskSpec(
            prompt="Operator-seeded exact project excerpts; no model", max_steps=20, max_turns=1
        )
    )
    records = []
    for name in RULES:
        path = f"docs/{name}"
        excerpt = next(
            p for p in (root / path).read_text().split("\n\n") if p.startswith("稳定项目约定：")
        ).strip()
        read = await runtime.step(ToolCall(name="read_file", arguments={"path": path}))
        loaded = await runtime.step(
            ToolCall(name="tool_load", arguments={"names": ["memory_store"]})
        )
        stored = await runtime.step(
            ToolCall(
                name="memory_store",
                arguments={"text": excerpt, "evidence_id": read.evidence_id, "tags": []},
            )
        )
        record = {
            "path": path,
            "text": excerpt,
            "read": read.model_dump(),
            "load": loaded.model_dump(),
            "store": stored.model_dump(),
        }
        records.append(record)
        if any(item.status != "success" for item in (read, loaded, stored)):
            write_once(output / "preparation-failure.json", records)
            raise ValueError("A grounded Memory admission failed; retain this attempt")
    runtime.finish("operator_seed_complete")
    if snapshot(root) != before:
        raise ValueError("Memory preparation changed product files")
    diagnostics = diagnose(root, output / "diagnostics", records)
    write_once(output / "diagnostics.json", diagnostics)
    files = [
        HERE / name
        for name in (
            "PROTOCOL.md",
            "memory-protocol.md",
            "memory_probe.py",
            "test_memory_probe.py",
            "run_project.py",
        )
    ]
    files += sorted((HERE / "questions").glob("*.md")) + sorted((HERE / "rules").glob("*.md"))
    cases = []
    for question, enabled in ORDER:
        name = f"memory-{question}-{'on' if enabled else 'off'}"
        command = [
            str(PYTHON),
            str(HERE / "run_project.py"),
            name,
            "--prompt",
            str(HERE / "questions" / f"{question}.md"),
            "--source-commit",
            source_commit,
            "--seed",
            str(root.resolve()),
            "--memory-seed",
            str(root.resolve()),
            "--read-only",
            "--steps",
            "8",
            "--turns",
            "6",
            "--seconds",
            "360",
        ]
        if enabled:
            command.append("--memory")
        cases.append(
            {
                "name": name,
                "question": question,
                "memory": enabled,
                "command": command,
                "shell_command": shlex.join(command),
            }
        )
    manifest = {
        "status": "prepared_not_run",
        "source_commit": source_commit,
        "memory_setup": "operator_seeded_not_autonomous_learning",
        "seed_product": before,
        "seed_all_files": snapshot(root, include_state=True),
        "files": {
            str(p.relative_to(HERE)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files
        },
        "records": records,
        "cases": cases,
    }
    write_once(output / "manifest.json", manifest)
    return manifest


def diagnose(seed, output, records):
    current = output / "current"
    shutil.copytree(seed, current)
    lesson_ids = {Path(row["path"]).name: row["store"]["data"]["id"] for row in records}
    rows = []
    for query, source in QUERIES:
        found = offline_runtime(current).memory.retrieve(query)
        ids = [item["id"] for item in found]
        rows.append(
            {
                "query": query,
                "expected_source": source,
                "ids": ids,
                "expected_recalled": lesson_ids[source] in ids,
            }
        )
    unrelated = offline_runtime(current).memory.retrieve("量子纠缠实验装置维护")
    stale = output / "changed-source"
    shutil.copytree(seed, stale)
    path = stale / "docs/data-contract.md"
    path.write_text(path.read_text().replace("campus-planner-v1", "campus-planner-v2"))
    found = offline_runtime(stale).memory.retrieve(QUERIES[0][0])
    return {
        "kind": "offline_mechanism_not_autonomous_learning",
        "queries": rows,
        "unrelated_ids": [item["id"] for item in unrelated],
        "changed_source": "docs/data-contract.md",
        "changed_source_old_lesson_absent": lesson_ids["data-contract.md"]
        not in [item["id"] for item in found],
        "seed_sources_unchanged": all(
            hashlib.sha256((seed / row["path"]).read_bytes()).hexdigest()
            == row["read"]["data"]["source_sha256"]
            for row in records
        ),
    }


def grade(question, final, records, *, observations=()):
    try:
        answer = json.loads(final)
    except (ValueError, TypeError):
        answer = None
    expected = ANSWERS[question]
    fields = {
        key: isinstance(answer, dict)
        and type(answer.get(key)) is type(value)
        and answer[key] == value
        for key, value in expected.items()
    }
    valid_refs = {row["path"]: row["path"] for row in records}
    valid_refs.update({f"evidence:{row['read']['evidence_id']}": row["path"] for row in records})
    source_hashes = {
        row["path"]: row["read"].get("data", {}).get("source_sha256") for row in records
    }
    for event in observations:
        outcome = event.get("outcome", {})
        data = outcome.get("data", {})
        path = data.get("path")
        if (
            event.get("event") == "tool_result"
            and event.get("call", {}).get("name") == "read_file"
            and outcome.get("status") == "success"
            and source_hashes.get(path)
            and data.get("source_sha256") == source_hashes[path]
        ):
            for reference in (outcome.get("evidence_id"), event.get("hash")):
                if isinstance(reference, str) and reference:
                    valid_refs[f"evidence:{reference}"] = path
    citations = answer.get("sources", []) if isinstance(answer, dict) else []
    refs_valid = (
        isinstance(citations, list)
        and bool(citations)
        and all(isinstance(ref, str) and ref in valid_refs for ref in citations)
    )
    covered = {valid_refs[ref] for ref in citations} if refs_valid else set()
    sources_passed = refs_valid and SOURCES[question].issubset(covered)
    return {
        "valid_json_object": isinstance(answer, dict),
        "fields": fields,
        "correct_fields": sum(fields.values()),
        "total_fields": len(fields),
        "sources_passed": sources_passed,
        "passed": all(fields.values()) and sources_passed,
    }


def text_values(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from text_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from text_values(item)


def audit(output):
    manifest = json.loads((output / "manifest.json").read_text())
    if snapshot(output / "seed", include_state=True) != manifest["seed_all_files"]:
        raise ValueError("Prepared seed changed after freezing")
    for name, sha in manifest["files"].items():
        if hashlib.sha256((HERE / name).read_bytes()).hexdigest() != sha:
            raise ValueError(f"Frozen experiment file changed: {name}")
    results = []
    for case in manifest["cases"]:
        result_path = HERE / "results" / f"{case['name']}.json"
        if not result_path.exists():
            results.append({**case, "status": "not_run"})
            continue
        result = json.loads(result_path.read_text())
        root = REPO / result["workspace"]
        events = [
            json.loads(line)
            for line in (root / ".aster/runs" / result["run_id"] / "trace.jsonl")
            .read_text()
            .splitlines()
        ]
        tools = [row for row in events if row["event"] == "tool_result"]
        contexts = [row for row in events if row["event"] == "context_built"]
        first = (
            json.loads((root / ".aster" / contexts[0]["request_artifact"]).read_text())
            if contexts
            else {}
        )
        first_text = "\n".join(text_values(first))
        recalled = [
            row["store"]["data"]["id"]
            for row in manifest["records"]
            if row["text"] in first_text and row["read"]["evidence_id"] in first_text
        ]
        reads = [row for row in tools if row["call"]["name"] == "read_file"]
        searches = [row for row in tools if row["call"]["name"] == "search"]
        requests = result.get("requests", [])
        correctness = grade(
            case["question"], result.get("final", ""), manifest["records"], observations=tools
        )
        unchanged = snapshot(root) == manifest["seed_product"]
        results.append(
            {
                "name": case["name"],
                "question": case["question"],
                "memory": case["memory"],
                "status": result["phase"],
                "grade": correctness,
                "passed": correctness["passed"] and unchanged and result["phase"] == "completed",
                "product_unchanged": unchanged,
                "first_request_recalled_ids": recalled,
                "read_file_calls": len(reads),
                "search_calls": len(searches),
                "source_read_file_calls": sum(
                    row["call"]["arguments"].get("path", "").removeprefix("./")
                    in {record["path"] for record in manifest["records"]}
                    for row in reads
                ),
                "search_arguments": [row["call"]["arguments"] for row in searches],
                "usage": result.get("usage"),
                "usage_known_requests": {
                    metric: sum(metric in (row.get("usage") or {}) for row in requests)
                    for metric in (
                        "input_tokens",
                        "output_tokens",
                        "cache_read_input_tokens",
                        "total_tokens",
                    )
                },
                "response_models": [row.get("response_model") for row in requests],
                "requests": len(requests),
                "turns": result["turns"],
                "steps": result["steps"],
                "seconds": result["seconds"],
            }
        )
    comparison = {
        "kind": "project_reference_QA_not_coding_benefit",
        "cases": results,
        "source_commit": manifest["source_commit"],
        "repetitions": 1,
    }
    write_once(output / "comparison.json", comparison)
    return comparison


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "audit"])
    parser.add_argument("--seed", type=Path)
    parser.add_argument("--source-commit", default="HEAD")
    args = parser.parse_args()
    if Path(sys.executable).resolve() != PYTHON.resolve():
        parser.error("Use the aster Python environment")
    if args.action == "prepare":
        if not args.seed:
            parser.error("prepare requires --seed pointing to an accepted product")
        commit = subprocess.check_output(
            ["git", "rev-parse", args.source_commit], cwd=REPO, text=True
        ).strip()
        result = asyncio.run(prepare(args.seed.resolve(), HERE / "memory", commit))
        print("\n".join(case["shell_command"] for case in result["cases"]))
    else:
        result = audit(HERE / "memory")
        print(json.dumps(result, ensure_ascii=False, indent=2))
