"""Textual UI: shared Runtime, explicit human decisions, cancellable background work."""

from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from pathlib import Path

from .config import Settings
from .runtime import Runtime
from .schema import TaskSpec

try:
    from textual.app import App, ComposeResult
    from textual.containers import Horizontal, Vertical
    from textual.screen import ModalScreen
    from textual.widgets import Button, Footer, Header, Input, RichLog, Static
    from textual.worker import WorkerCancelled, WorkerFailed
except ImportError:  # pragma: no cover
    App = None


if App is not None:

    class HumanPrompt(ModalScreen[str | None]):
        """Escape always declines; a permission prompt never has an implicit yes."""

        BINDINGS = [("escape", "decline", "Cancel")]
        CSS = """
        HumanPrompt { align: center middle; }
        #decision { width: 85%; max-width: 100; height: auto; padding: 1 2;
                    border: round $accent; background: $surface; }
        #decision-text { max-height: 20; overflow-y: auto; }
        #decision-buttons { height: auto; align-horizontal: right; margin-top: 1; }
        """

        def __init__(self, text: str, *, approval: bool = False):
            super().__init__()
            self.text, self.approval = text, approval

        def compose(self) -> ComposeResult:
            with Vertical(id="decision"):
                yield Static(self.text, id="decision-text", markup=False)
                if not self.approval:
                    yield Input(placeholder="Your answer", id="answer")
                with Horizontal(id="decision-buttons"):
                    yield Button("Deny" if self.approval else "Cancel", id="decline")
                    yield Button(
                        "Allow once" if self.approval else "Send", id="accept", variant="primary"
                    )

        def on_mount(self) -> None:
            self.query_one("#decline" if self.approval else "#answer").focus()

        def action_decline(self) -> None:
            self.dismiss(None)

        def _accept(self) -> None:
            answer = "yes" if self.approval else self.query_one("#answer", Input).value.strip()
            if answer:
                self.dismiss(answer)

        def on_button_pressed(self, event: Button.Pressed) -> None:
            if event.button.id == "accept":
                self._accept()
            else:
                self.action_decline()

        def on_input_submitted(self, event: Input.Submitted) -> None:
            event.stop()
            self._accept()

    class AsterTui(App):
        CSS = "Screen { layout: vertical; } #log { height: 1fr; } #progress { height: auto; }"
        BINDINGS = [("ctrl+c", "cancel_task", "Cancel task"), ("ctrl+q", "quit", "Quit")]

        def __init__(self, root: Path, settings: Settings):
            super().__init__()
            self.root_path, self.settings = root, settings
            self.runtime = Runtime(
                root, settings, approval_callback=self._approve, question_callback=self._question
            )
            self.task_worker = None
            self._interaction_lock = asyncio.Lock()

        def compose(self) -> ComposeResult:
            yield Header()
            yield RichLog(id="log", wrap=True, markup=False)
            yield Static("Aster ready", id="progress", markup=False)
            yield Input(placeholder="Describe the coding task", id="prompt")
            yield Footer()

        def on_mount(self) -> None:
            self.runtime.evidence.listeners.append(self._event)
            self.query_one("#prompt", Input).focus()

        def _event(self, row: dict) -> None:
            event = row["event"]
            if event == "tool_result":
                outcome = row["outcome"]
                self.query_one("#log", RichLog).write(
                    f"{row['call']['name']}: {outcome['status']} {outcome.get('reason', '')}"
                )
            elif event == "model_reply" and row.get("text"):
                self.query_one("#log", RichLog).write(row["text"])
            elif event == "worker_started":
                self.query_one("#log", RichLog).write(f"Worker started: {row['worker_id']}")
            state = self.runtime.state
            if state:
                self.query_one("#progress", Static).update(
                    f"{state.phase} | turn {state.turn}/{state.task.max_turns} | "
                    f"tools {state.step}/{state.task.max_steps} | "
                    f"verification: {state.verification.state}"
                )

        async def _prompt(self, screen: HumanPrompt) -> str | None:
            # Parent and workers share one human; do not stack competing decisions.
            async with self._interaction_lock:
                try:
                    return await self.push_screen_wait(screen)
                finally:
                    if self.screen is screen:
                        self.pop_screen()

        async def _approve(self, call, decision) -> bool:
            arguments = self.runtime.evidence.redactor.value(call.arguments)
            text = f"Allow {call.name} once?\n{decision.reason}\n{json.dumps(arguments, ensure_ascii=False, indent=2)}"
            return await self._prompt(HumanPrompt(text, approval=True)) == "yes"

        async def _question(self, question: str) -> str | None:
            return await self._prompt(HumanPrompt(question))

        def on_input_submitted(self, event: Input.Submitted) -> None:
            prompt = event.value.strip()
            if not prompt or (self.task_worker and not self.task_worker.is_finished):
                return
            event.input.value = ""
            event.input.disabled = True
            self.query_one("#log", RichLog).write(f"Task: {prompt}")
            self.task_worker = self.run_worker(self._run_task(prompt), exit_on_error=False)

        async def _run_task(self, prompt: str) -> None:
            try:
                state = await self.runtime.run(TaskSpec(prompt=prompt))
                self.query_one("#log", RichLog).write(
                    state.final or f"Stopped: {state.stop_reason}\n{state.feedback}"
                )
            except asyncio.CancelledError:
                if self.runtime.state and not self.runtime.state.terminal:
                    self.runtime.abort()
                raise
            except Exception as exc:
                if self.runtime.state and not self.runtime.state.terminal:
                    self.runtime.finish("ui_error", failed=True)
                self.query_one("#log", RichLog).write(f"Run failed: {type(exc).__name__}")
            finally:
                self.query_one("#prompt", Input).disabled = False
                self.query_one("#prompt", Input).focus()

        async def action_cancel_task(self) -> None:
            if self.task_worker and not self.task_worker.is_finished:
                self.task_worker.cancel()
                with suppress(WorkerCancelled, WorkerFailed):
                    await self.task_worker.wait()

        async def on_unmount(self) -> None:
            if self._event in self.runtime.evidence.listeners:
                self.runtime.evidence.listeners.remove(self._event)
            await self.action_cancel_task()
else:

    class AsterTui:
        def __init__(self, *_args, **_kwargs):
            raise RuntimeError("TUI requires textual")

        def run(self):
            raise RuntimeError("TUI requires textual")
