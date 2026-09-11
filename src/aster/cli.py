"""Minimal CLI: one-shot runs, RL smoke checks, and evidence inspection."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from .config import load_settings
from .rl import AsterEnv
from .runtime import Runtime
from .schema import TaskSpec


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
    if args.rl_smoke:
        return asyncio.run(
            _rl_smoke(Path(args.cwd), settings, args.prompt or "inspect the workspace")
        )
    if not args.prompt:
        parser.error("prompt is required")
    runtime = Runtime(
        Path(args.cwd),
        settings,
        approval_callback=lambda call, decision: _ask(call.name, decision.reason),
    )
    state = asyncio.run(runtime.run(TaskSpec(prompt=args.prompt)))
    print(state.final or f"Aster stopped: {state.stop_reason}\n{state.feedback}")
    print(f"run_id={state.run_id} steps={state.step} verification={state.verification.state}")
    return 0 if state.phase == "completed" else 1


async def _rl_smoke(root, settings, prompt):
    env = AsterEnv(root, settings)
    observation, info = env.reset(TaskSpec(prompt=prompt, max_steps=1))
    print({"observation": observation, "info": info})
    return 0


def _ask(name, reason):
    answer = input(f"Allow {name} ({reason})? [y/N] ")
    return answer.casefold() in {"y", "yes"}
