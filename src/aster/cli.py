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
    parser.add_argument("--model")
    parser.add_argument("--protocol", choices=["responses", "chat", "anthropic"])
    parser.add_argument("--sandbox", choices=["required", "best_effort", "off"])
    parser.add_argument("--approval", choices=["ask", "auto", "never"])
    parser.add_argument("--read-only", action="store_true")
    parser.add_argument("--rl-smoke", action="store_true")
    parser.add_argument("--tui", action="store_true")
    args = parser.parse_args(argv)
    env_file = args.env_file or (Path.cwd() / ".env")
    settings = load_settings(
        env_file if env_file.exists() else None,
        args.config,
        model=args.model,
        protocol=args.protocol,
        sandbox=args.sandbox,
        approval=args.approval,
        read_only=args.read_only or None,
    )
    context_source = {
        "explicit": "configured total budget",
        "model_profile": "model default with 5% estimation margin",
        "fallback": "unknown model fallback; set context_tokens in your config to override",
    }[settings.context_budget_source]
    print(
        f"Aster context: {settings.effective_context_tokens - settings.output_tokens:,} input "
        f"+ {settings.output_tokens:,} output tokens ({context_source}).",
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
        app.sub_title = (
            f"{label} | {settings.effective_context_tokens - settings.output_tokens:,} input tokens"
        )
        app.run()
        return 0
    if args.rl_smoke:
        return asyncio.run(
            _rl_smoke(Path(args.cwd), settings, args.prompt or "inspect the workspace")
        )
    if not args.prompt:
        parser.error("prompt is required")
    terminal = Terminal(settings)
    runtime = Runtime(
        Path(args.cwd),
        settings,
        approval_callback=terminal.approve,
        question_callback=terminal.question,
    )
    try:
        state = asyncio.run(runtime.run(TaskSpec(prompt=args.prompt)))
    except KeyboardInterrupt:
        print("Aster cancelled.")
        return 130
    print(state.final or f"Aster stopped: {state.stop_reason}\n{state.feedback}")
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
