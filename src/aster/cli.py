"""Minimal CLI: one-shot runs, RL smoke checks, and evidence inspection."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import stat
import sys
from pathlib import Path

from .config import load_settings
from .evidence import Redactor
from .rl import AsterEnv
from .runtime import Runtime
from .schema import TaskSpec

try:
    from .tui import AsterTui
except ImportError:  # pragma: no cover
    AsterTui = None


def main(argv=None):
    parser = argparse.ArgumentParser(prog="aster")
    parser.add_argument("prompt", nargs="?")
    parser.add_argument("--cwd", default=".")
    parser.add_argument("--env-file", type=Path, default=None)
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--profile", help="Named model profile from --config")
    parser.add_argument("--model")
    parser.add_argument("--context-tokens", type=int, help="Total input plus output token budget")
    parser.add_argument("--output-tokens", type=int, help="Maximum tokens per model response")
    parser.add_argument("--max-steps", type=int, help="Tool action cap for a one-shot task")
    parser.add_argument("--max-turns", type=int, help="Model turn cap for a one-shot task")
    parser.add_argument(
        "--resume", metavar="RUN_ID", help="Continue a saved run, optionally with new feedback"
    )
    parser.add_argument(
        "--allow-model-change",
        action="store_true",
        help="Explicitly convert saved history to the selected model",
    )
    parser.add_argument(
        "--adopt-workspace",
        action="store_true",
        help="Adopt external edits when continuing; fresh reads and tests are required",
    )
    parser.add_argument("--protocol", choices=["responses", "chat", "anthropic"])
    parser.add_argument("--stream", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--sandbox", choices=["required", "best_effort", "off"])
    parser.add_argument("--approval", choices=["ask", "auto", "never"])
    parser.add_argument("--read-only", action="store_true")
    parser.add_argument("--rl-smoke", action="store_true")
    parser.add_argument("--tui", action="store_true")
    args = parser.parse_args(argv)
    if (args.allow_model_change or args.adopt_workspace) and not args.resume:
        parser.error("--allow-model-change and --adopt-workspace require --resume")
    if args.resume and (args.tui or args.rl_smoke):
        parser.error("--resume is a one-shot option; use /resume inside TUI")
    if (args.tui or args.rl_smoke) and (args.max_steps is not None or args.max_turns is not None):
        parser.error("--max-steps and --max-turns apply only to one-shot tasks")
    env_file = args.env_file or (Path.cwd() / ".env")
    try:
        settings = load_settings(
            env_file if env_file.exists() else None,
            args.config,
            profile=args.profile,
            model=args.model,
            context_tokens=args.context_tokens,
            output_tokens=args.output_tokens,
            protocol=args.protocol,
            stream=args.stream,
            sandbox=args.sandbox,
            approval=args.approval,
            read_only=args.read_only or None,
        )
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    context_source = {
        "explicit": "configured total budget",
        "model_profile": "model default with 5% estimation margin",
        "fallback": "unknown model fallback; set context_tokens in your config to override",
    }[settings.context_budget_source]
    output_source = {
        "explicit": "configured",
        "context_budget": "automatic, up to one quarter of the configured total",
        "model_profile": "automatic 32,768 ceiling, within model limit",
        "fallback": "unknown model fallback; use --output-tokens to override",
    }[settings.output_budget_source]
    output = settings.effective_output_tokens
    print(
        f"Aster model: {settings.profile or '(legacy)'} | {settings.provider}/{settings.model} "
        f"| {settings.protocol} | stream={settings.stream}.",
        file=sys.stderr,
    )
    print(
        f"Aster context: {settings.effective_context_tokens - output:,} input "
        f"+ {output:,} output tokens ({context_source}); output: {output_source}.",
        file=sys.stderr,
    )
    if args.tui:
        if AsterTui is None:
            parser.error("TUI requires textual")
        app = AsterTui(Path(args.cwd), settings)
        label = (
            "Unknown model fallback"
            if settings.context_budget_source == "fallback"
            else settings.model
        )
        app.sub_title = f"{label} | {settings.effective_context_tokens - output:,} input tokens"
        app.run()
        return 0
    if args.rl_smoke:
        return asyncio.run(
            _rl_smoke(Path(args.cwd), settings, args.prompt or "inspect the workspace")
        )
    if not args.prompt and not args.resume:
        parser.error("prompt is required")
    terminal = Terminal(settings)
    runtime = Runtime(
        Path(args.cwd),
        settings,
        approval_callback=terminal.approve,
        question_callback=terminal.question,
    )
    try:
        task_limits = {
            name: value
            for name, value in {"max_steps": args.max_steps, "max_turns": args.max_turns}.items()
            if value is not None
        }
        if args.resume:
            runtime.resume(
                args.resume,
                allow_model_change=args.allow_model_change,
                adopt_workspace=args.adopt_workspace,
            )
            runtime.continue_task(args.prompt, adopt_workspace=args.adopt_workspace, **task_limits)
            state = asyncio.run(runtime.run())
        else:
            state = asyncio.run(runtime.run(TaskSpec(prompt=args.prompt, **task_limits)))
    except (OSError, ValueError, RuntimeError) as exc:
        print(runtime.evidence.redactor.text(f"Aster: {exc}"), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Aster cancelled.")
        return 130
    finally:
        if settings.reuse_environment:
            runtime.sandbox.close()
    print(
        runtime.evidence.redactor.text(
            state.final or f"Aster stopped: {state.stop_reason}\n{state.feedback}"
        )
    )
    print(f"run_id={state.run_id} steps={state.step} verification={state.verification.state}")
    return 0 if state.phase == "completed" else 1


def tui_main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    return main(["--tui", *argv])


async def _rl_smoke(root, settings, prompt):
    env = AsterEnv(root, settings)
    try:
        observation, info = env.reset(TaskSpec(prompt=prompt, max_steps=1))
        print({"observation": observation, "info": info})
    finally:
        env.close()
    return 0


class Terminal:
    """One human input at a time, without blocking worker progress or cancellation."""

    def __init__(self, settings):
        self.lock = asyncio.Lock()
        self.redactor = Redactor([settings.api_key.get_secret_value()])
        self._input_buffer = b""

    async def approve(self, call, decision):
        arguments = json.dumps(self.redactor.value(call.arguments), ensure_ascii=False)
        answer = await self.question(f"Allow {call.name} {arguments} ({decision.reason})? [y/N]")
        return bool(answer and answer.casefold() in {"y", "yes"})

    async def question(self, question):
        async with self.lock:
            print(question, end=" ", flush=True)
            fd = sys.stdin.fileno()
            # Redirected regular files are immediately readable and cannot use epoll.
            regular_file = stat.S_ISREG(os.fstat(fd).st_mode)
            if regular_file:
                line = sys.stdin.readline()
            elif b"\n" not in self._input_buffer:
                loop = asyncio.get_running_loop()
                future = loop.create_future()

                def ready():
                    if future.done():
                        return
                    chunk = os.read(fd, 4096)
                    self._input_buffer += chunk
                    if b"\n" in self._input_buffer or not chunk:
                        future.set_result(None)

                try:
                    loop.add_reader(fd, ready)
                except (PermissionError, OSError):
                    return None  # /dev/null and closed headless input cannot answer.
                try:
                    await future
                finally:
                    loop.remove_reader(fd)
            if not regular_file:
                raw, _, self._input_buffer = self._input_buffer.partition(b"\n")
                line = raw.decode(sys.stdin.encoding or "utf-8", errors="replace")
            return line.strip() if line else None
