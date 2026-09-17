"""Run the website task through Aster and preserve operator-visible evidence."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import time
from pathlib import Path

from website_stream_transport import ResponsesStreamingClient

from aster.config import load_settings
from aster.runtime import Runtime
from aster.schema import TaskSpec


async def run(args):
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    settings = load_settings(
        args.env_file,
        approval="auto",
        sandbox="required",
        network=False,
        write_scope=args.write_scope or ["learn_web"],
        output_tokens=args.output_tokens,
        context_tokens=args.context_tokens,
        request_timeout=300,
        max_workers=0,
        memory_enabled=False,
        auto_dream=False,
        **({"model": args.model} if args.model else {}),
    )
    prompt = args.prompt.read_text()
    (output / "prompt.md").write_text(prompt)
    # The user authorized this task; even compound commands remain sandboxed and scoped.
    model = ResponsesStreamingClient(settings, output / "transport.jsonl") if args.experimental_stream else None
    runtime = Runtime(args.workspace.resolve(), settings, model=model, approval_callback=lambda *_: True)
    started = time.monotonic()
    log = output / "events.jsonl"

    def event(row):
        with log.open("a") as stream:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
        if row["event"] == "context_built":
            print(f"request: {row['estimated_tokens']} estimated tokens", flush=True)
        elif row["event"] == "tool_result":
            result = row["outcome"]
            print(
                f"step {row['step']}: {row['call']['name']} "
                f"{result['status']} {result.get('reason', '')}",
                flush=True,
            )
        elif row["event"] in {"run_stopped", "run_completed", "provider_error"}:
            print(f"event: {row['event']}", flush=True)

    runtime.evidence.listeners.append(event)
    task = TaskSpec(
        prompt=prompt,
        max_steps=args.max_steps,
        max_turns=args.max_turns,
        require_observation=True,
        require_edit=True,
        require_verification=not args.skip_verification,
        required_artifacts=args.required_artifact or ["learn_web/index.html", "learn_web/README.md", "learn_web/.nojekyll"],
    )
    launch = {
        "python": sys.executable,
        "workspace": str(args.workspace.resolve()),
        "configuration": settings.identity(),
        "task": task.model_dump(),
        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "initial_workspace_hash": runtime.workspace.fingerprint(),
        "operator": "Codex; all website implementation must be produced by Aster",
        "transport": "experimental SSE, reasoning=low" if args.experimental_stream else "product non-streaming",
    }
    (output / "launch.json").write_text(json.dumps(launch, ensure_ascii=False, indent=2))
    error = ""
    try:
        await runtime.run(task)
    except Exception as exc:
        error = runtime.evidence.redactor.text(f"{type(exc).__name__}: {exc}")
    finally:
        state = runtime.state
        summary = {
            "run_id": state.run_id if state else None,
            "phase": state.phase if state else "failed",
            "stop_reason": state.stop_reason if state else "startup_error",
            "feedback": state.feedback if state else "",
            "steps": state.step if state else 0,
            "turns": state.turn if state else 0,
            "usage": state.usage if state else {},
            "duration_seconds": round(time.monotonic() - started, 2),
            "changed_paths": state.changed_paths if state else [],
            "verification": state.verification.model_dump() if state else None,
            "final": state.final if state else "",
            "error": error,
        }
        (output / "summary.json").write_text(
            json.dumps(runtime.evidence.redactor.value(summary), ensure_ascii=False, indent=2)
        )
        display = {**summary, "changed_path_count": len(summary["changed_paths"])}
        display.pop("changed_paths")
        print(json.dumps(runtime.evidence.redactor.value(display), ensure_ascii=False, indent=2), flush=True)
    return 0 if state and state.phase == "completed" and not error else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--prompt", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--model", help="Override the model for a recorded follow-up run.")
    parser.add_argument("--context-tokens", type=int, help="Explicit total task context budget.")
    parser.add_argument("--output-tokens", type=int, default=8192)
    parser.add_argument("--write-scope", action="append")
    parser.add_argument("--required-artifact", action="append")
    parser.add_argument("--skip-verification", action="store_true", help="Intermediate stage only; operator must verify the integrated site.")
    parser.add_argument("--experimental-stream", action="store_true", help="Operator-only SSE adapter with low reasoning; not native product streaming.")
    parser.add_argument("--max-steps", type=int, default=140)
    parser.add_argument("--max-turns", type=int, default=100)
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
