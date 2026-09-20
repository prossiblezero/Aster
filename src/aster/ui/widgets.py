"""Prompt editor and explicit human approval/question widgets."""
from __future__ import annotations

try:
    from textual.app import ComposeResult
    from textual.binding import Binding
    from textual.containers import Horizontal, Vertical
    from textual.screen import ModalScreen
    from textual.widgets import Button, Input, Static
except ImportError:  # pragma: no cover
    Input = None

if Input is not None:
    class PromptInput(Input):
        BINDINGS = [
            Binding("tab", "complete_command", show=False),
            Binding("up", "previous_entry", show=False),
            Binding("down", "next_entry", show=False),
        ]

        def action_complete_command(self) -> None:
            self.app.complete_command()

        def action_previous_entry(self) -> None:
            self.app.move_input(-1)

        def action_next_entry(self) -> None:
            self.app.move_input(1)

    class HumanPrompt(ModalScreen[str | None]):
        """Escape declines; a permission prompt never has an implicit yes."""

        BINDINGS = [("escape", "decline", "取消")]
        CSS = """
        HumanPrompt { align: center middle; background: #0f1117 80%; }
        #decision { width: 85%; max-width: 100; height: auto; padding: 1 2;
                    border: round #9b8afb; background: #191c26; }
        #decision-text { max-height: 20; overflow-y: auto; }
        #decision-buttons { height: auto; align-horizontal: right; margin-top: 1; }
        #decision-buttons Button { min-width: 6; width: auto; padding: 0 1; margin-left: 1; }
        """

        def __init__(self, text: str, *, approval: bool = False, value="", accept_label=None):
            super().__init__()
            self.text, self.approval = text, approval
            self.value, self.accept_label = value, accept_label

        def compose(self) -> ComposeResult:
            with Vertical(id="decision"):
                yield Static(self.text, id="decision-text", markup=False)
                if not self.approval:
                    yield Input(value=self.value, placeholder="请输入你的回答", id="answer")
                with Horizontal(id="decision-buttons"):
                    yield Button("拒绝" if self.approval else "取消", id="decline")
                    yield Button(
                        self.accept_label or ("仅允许本次" if self.approval else "发送"), id="accept", variant="primary"
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
            event.stop()
            if event.button.id == "accept":
                self._accept()
            else:
                self.action_decline()

        def on_input_submitted(self, event: Input.Submitted) -> None:
            event.stop()
            self._accept()


else:  # pragma: no cover
    PromptInput = HumanPrompt = None
