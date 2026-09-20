"""Clickable session controls and shared transcript rendering."""
from __future__ import annotations

from rich.markdown import Markdown
from rich.style import Style
from rich.text import Text
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, OptionList, RichLog, Static
from textual.widgets.option_list import Option


class TranscriptLog(RichLog):
    """RichLog keeps rendered lines; replay them when its available width changes."""

    _transcript_width = 0

    def on_resize(self, event):
        if event.size.width != self._transcript_width:
            self._transcript_width = event.size.width
            self.call_after_refresh(self.app._redraw_transcript, True)


def render_log(text, style=""):
    if style.startswith("branch:"):
        entry = style.partition(":")[2]
        result = Text(text + "  ", style="#8f9cb4")
        for label, summary in [("从此分支", False), ("带入摘录", True)]:
            result.append(f"〔{label}〕", style=Style(
                color="#b7b0ff", underline=True,
                meta={"@click": ("app.branch_at", (entry, summary))},
            ))
            result.append(" ")
        return result
    return Markdown(text) if style == "markdown" else Text(text, style=style)


def session_status(phase, reason=""):
    if reason == "cancelled":
        return "已取消"
    if reason == "branch_selected":
        return "待输入"
    return {"running": "待恢复", "completed": "已完成", "stopped": "已停止",
            "failed": "失败", "ready": "就绪"}.get(phase, "就绪")


class SessionPicker(ModalScreen[tuple[str, bool] | None]):
    BINDINGS = [("escape", "dismiss_picker", "返回"), ("ctrl+s", "with_summary", "带入摘录")]
    CSS = """
    SessionPicker { align: center middle; background: #0f1117 85%; }
    #session-picker { width: 90%; height: 85%; border: round #9b8afb; padding: 1 2; background: #191c26; }
    SessionPicker.compact #session-picker { height: 95%; padding: 0 1; }
    #session-choices { height: 1fr; min-height: 3; }
    #picker-title, #picker-hint { height: 1; }
    #session-query { height: 3; }
    #picker-buttons { height: auto; align-horizontal: right; }
    #picker-buttons Button { min-width: 8; margin-left: 1; }
    """

    def __init__(self, rows, *, title, query="", summaries=False, refresh_rows=None):
        super().__init__()
        self.rows, self.title, self.initial_query, self.summaries = rows, title, query, summaries
        self.refresh_rows = refresh_rows

    def compose(self):
        with Vertical(id="session-picker"):
            yield Static(self.title, id="picker-title", markup=False)
            yield Input(value=self.initial_query, placeholder="关键词或编号筛选（最多显示 300 项）；↓ 选择，Enter 打开", id="session-query")
            yield OptionList(id="session-choices", markup=False)
            yield Static("Esc 返回" + (" · Ctrl+S 带入当前分支摘录 · 文件不会回滚" if self.summaries else ""), id="picker-hint")
            with Horizontal(id="picker-buttons"):
                yield Button("返回", id="picker-close")
                if self.summaries:
                    yield Button("带入摘录", id="picker-summary")
                yield Button("选择", id="picker-select", variant="primary")

    def on_mount(self):
        self.set_class(self.app.size.height < 24, "compact")
        self.filter(self.initial_query)
        self.query_one(Input).focus()
        if self.refresh_rows:
            self.set_interval(0.5, self.refresh_choices)

    def on_resize(self, event):
        self.set_class(event.size.height < 24, "compact")

    def refresh_choices(self):
        rows = self.refresh_rows()
        if rows != self.rows:
            self.rows = rows
            self.filter(self.query_one(Input).value)

    def filter(self, query):
        choices = self.query_one(OptionList)
        selected = choices.get_option_at_index(choices.highlighted).id if choices.highlighted is not None else None
        choices.clear_options()
        rows = [row for row in self.rows if query.casefold() in row[1].casefold() or query in row[0]][:300]
        choices.add_options(Option(text, id=identifier) for identifier, text in rows)
        choices.highlighted = next((i for i, row in enumerate(rows) if row[0] == selected), 0) if rows else None

    def on_button_pressed(self, event: Button.Pressed):
        event.stop()
        if event.button.id == "picker-close":
            self.dismiss(None)
        else:
            self.selected(event.button.id == "picker-summary")

    def on_input_changed(self, event: Input.Changed):
        self.filter(event.value)

    def on_key(self, event):
        if event.key == "down" and isinstance(self.focused, Input):
            self.query_one(OptionList).focus()
            event.stop()

    def selected(self, summary=False):
        choices = self.query_one(OptionList)
        if choices.highlighted is not None:
            self.dismiss((choices.get_option_at_index(choices.highlighted).id, summary))

    def on_input_submitted(self, event: Input.Submitted):
        event.stop()
        self.selected()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected):
        self.dismiss((event.option.id, False))

    def action_dismiss_picker(self):
        self.dismiss(None)

    def action_with_summary(self):
        if self.summaries:
            self.selected(True)
