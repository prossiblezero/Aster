"""Small Textual presentation layer over the shared Runtime."""

from __future__ import annotations

from pathlib import Path

from .config import Settings
from .runtime import Runtime
from .schema import TaskSpec

try:
    from textual.app import App, ComposeResult
    from textual.containers import VerticalScroll
    from textual.widgets import Footer, Header, Input, Static
except ImportError:  # pragma: no cover
    App = None


if App is not None:
    class AsterTui(App):
        CSS = "Screen { layout: vertical; } #log { height: 1fr; }"

        def __init__(self, root: Path, settings: Settings):
            super().__init__()
            self.root_path, self.settings = root, settings
            self.runtime = Runtime(root, settings, approval_callback=lambda *_: True)

        def compose(self) -> ComposeResult:
            yield Header()
            yield VerticalScroll(Static("Aster ready", id="log"))
            yield Input(placeholder="Describe the coding task", id="prompt")
            yield Footer()

        async def on_input_submitted(self, event: Input.Submitted) -> None:
            event.input.value = ""
            log = self.query_one("#log", Static)
            log.update(f"Running: {event.value}")
            state = await self.runtime.run(TaskSpec(prompt=event.value))
            log.update(state.final or f"Stopped: {state.stop_reason}\n{state.feedback}")
else:
    class AsterTui:
        def __init__(self, *_args, **_kwargs):
            raise RuntimeError("TUI requires textual")

        def run(self):
            raise RuntimeError("TUI requires textual")
