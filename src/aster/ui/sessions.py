"""Session navigation and independent live views for the terminal UI."""
from __future__ import annotations

import json
import shlex
from pathlib import Path

from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, RichLog, Static
from textual.widgets.option_list import Option

from ..core.runtime import Runtime
from ..providers.client import ModelClient
from .widgets import HumanPrompt


class SessionPicker(ModalScreen[tuple[str, bool] | None]):
    BINDINGS = [("escape", "dismiss_picker", "返回"), ("ctrl+s", "with_summary", "带入摘录")]
    CSS = """
    SessionPicker { align: center middle; background: #0f1117 85%; }
    #session-picker { width: 90%; height: 85%; border: round #9b8afb; padding: 1 2; background: #191c26; }
    #session-choices { height: 1fr; }
    #session-query { height: 3; }
    """

    def __init__(self, rows, *, title, query="", summaries=False):
        super().__init__()
        self.rows, self.title, self.initial_query, self.summaries = rows, title, query, summaries

    def compose(self):
        with Vertical(id="session-picker"):
            yield Static(self.title, markup=False)
            yield Input(value=self.initial_query, placeholder="输入关键词或编号筛选；↓ 选择，Enter 打开", id="session-query")
            yield OptionList(id="session-choices", markup=False)
            yield Static("Esc 返回" + (" · Ctrl+S 带入当前分支摘录 · 文件不会回滚" if self.summaries else ""))

    def on_mount(self):
        self.filter(self.initial_query)
        self.query_one(Input).focus()

    def filter(self, query):
        choices = self.query_one(OptionList)
        choices.clear_options()
        rows = [row for row in self.rows if query.casefold() in row[1].casefold() or query in row[0]]
        choices.add_options(Option(text, id=identifier) for identifier, text in rows[:300])
        choices.highlighted = 0 if rows else None

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


VIEW_FIELDS = (
    "runtime", "settings", "root_path", "task_worker", "_activity", "_started_at", "_mode",
    "_context_report", "_stream_text", "_reasoning_text", "_stream_status", "_last_model_text", "_stream_active",
)


class SessionViews:
    """UI mixin; each asynchronous run captures its own Runtime and event destination."""

    def _init_views(self):
        self._views = []
        self._register_view(self.runtime)

    def _register_view(self, runtime):
        view = {name: getattr(self, name) for name in VIEW_FIELDS}
        view.update(runtime=runtime, log=[], events=[])
        def listener(row):
            self._route_session_event(runtime, row)
        view["listener"] = listener
        runtime.evidence.listeners.append(listener)
        runtime.approval_callback = lambda call, decision: self._session_approve(runtime, call, decision)
        runtime.question_callback = lambda question: self._session_question(runtime, question)
        self._views.append(view)
        return view

    def _view(self, runtime=None):
        return next(view for view in self._views if view["runtime"] is (runtime or self.runtime))

    def _save_view(self):
        self._view().update({name: getattr(self, name) for name in VIEW_FIELDS})
        if self.screen_stack and self.screen_stack[0].query("#prompt"):
            self._view()["draft"] = self._main_query("#prompt", Input).value

    def _route_session_event(self, runtime, row):
        if runtime is self.runtime:
            self._event(row)
        else:
            # Do not retain streaming deltas: the final model_reply contains the answer.
            if row["event"] != "model_stream":
                view = self._view(runtime)
                view["events"].append(row)
                view["events"] = view["events"][-2000:]

    async def _session_approve(self, runtime, call, decision):
        arguments = runtime.evidence.redactor.value(call.arguments)
        text = f"会话 {runtime.state.session_id[:8]} · {runtime.workspace.root}\n是否仅允许本次 {call.name} 操作？\n{decision.reason}\n{json.dumps(arguments, ensure_ascii=False, indent=2)}"
        return await self._prompt(HumanPrompt(text, approval=True)) == "yes"

    async def _session_question(self, runtime, question):
        return await self._prompt(HumanPrompt(f"会话 {runtime.state.session_id[:8]} · {runtime.workspace.root}\n{question}"))

    def _switch_view(self, view):
        self._save_view()
        for name in VIEW_FIELDS:
            setattr(self, name, view[name])
        log = self._main_query("#log", RichLog)
        log.clear()
        from rich.markdown import Markdown
        from rich.text import Text
        for text, style in view["log"]:
            log.write(Markdown(text) if style == "markdown" else Text(text, style=style))
        events, view["events"] = view["events"], []
        for event in events:
            self._event(event)
        self._main_query("#prompt", Input).value = view.get("draft", "")
        self._main_query("#model-output", Static).update(self._stream_text)
        self._main_query("#model-output").display = self._stream_active and self.busy
        self._main_query("#reasoning").display = False
        self._refresh_model()
        self._tick()

    def _open_session(self, root=None):
        self._save_view()
        target = Path(root).expanduser().resolve() if root else self.root_path
        runtime = Runtime(target, self.settings.model_copy(deep=True))
        if not isinstance(self.runtime.model, ModelClient):
            runtime.model = self.runtime.model
        self.runtime, self.root_path, self.settings = runtime, target, runtime.settings
        self.task_worker = None
        self._activity, self._mode, self._context_report = "就绪", "work", None
        self._stream_text = self._reasoning_text = self._stream_status = self._last_model_text = ""
        self._stream_active = False
        self._register_view(runtime)
        self.action_clear_screen()
        self._refresh_model()
        self._write(f"已打开独立会话 · {target}\n输入任务开始；/sessions 切换会话。")

    def _select_entry(self, result):
        if result is None:
            return
        entry, summary = result
        try:
            self.runtime.checkout(entry, summary=summary)
            self._after_checkout()
        except (OSError, ValueError, RuntimeError) as exc:
            self._write(f"未切换：{exc}\n如需保留当前文件并采用历史上下文，使用 /branch {entry} --adopt-workspace。", style="#efb38f")

    def _after_checkout(self):
        self.settings = self.runtime.settings
        self._mode = self.runtime.state.mode
        self._context_report = None
        self._activity = "已选择历史位置"
        self.action_clear_screen()
        for message in self.runtime._structured_history[-20:]:
            if message.get("role") in {"user", "assistant"}:
                content = message.get("content", "")
                if isinstance(content, list):
                    content = "\n".join(block.get("text", "") for block in content if isinstance(block, dict))
                self._write(f"{'你' if message['role'] == 'user' else 'Aster'}：{content}")
        self._write(f"会话 {self.runtime.state.session_id[:12]} · 位置 {self.runtime.session_tree.cursor[:12]}\n历史和模型已恢复，文件保持当前状态。输入新请求会从此处继续。")
        self._refresh_model()
        self._tick()

    def _select_session(self, result):
        if result is None:
            return
        reference = result[0]
        try:
            if reference.startswith("live:"):
                self._switch_view(self._views[int(reference[5:])])
            else:
                self._checkout_session(reference)
        except (ValueError, RuntimeError, OSError) as exc:
            self._write(f"未切换：{exc}", style="#efb38f")

    def _checkout_session(self, reference, *, adopt_workspace=False):
        for view in self._views:
            state = view["runtime"].state
            if state and state.session_id == reference:
                self._switch_view(view)
                return
        head = self.runtime.session_tree.session_head(reference)
        data = self.runtime.session_tree.restore(head)
        for view in self._views:
            if view["runtime"].state and view["runtime"].state.session_id == data["state"]["session_id"]:
                self._switch_view(view)
                return
        # Validate before installing another view, keeping current work intact on failure.
        restored = Runtime(self.root_path, self.settings.model_copy(deep=True))
        restored.checkout(head, adopt_workspace=adopt_workspace)
        self._save_view()
        self.runtime, self.settings = restored, restored.settings
        self.task_worker = None
        self._stream_active = False
        self._register_view(restored)
        self._after_checkout()

    def _session_command(self, command, argument):
        if command == "/new":
            args = shlex.split(argument)
            if len(args) > 1:
                raise ValueError('用法：/new ["工作目录"]')
            self._open_session(args[0] if args else None)
        elif command == "/sessions":
            self._save_view()
            rows, opened = [], set()
            titles = {row["id"]: row["title"] for row in self.runtime.session_tree.sessions()}
            for index, view in enumerate(self._views):
                runtime, state = view["runtime"], view["runtime"].state
                title = (titles.get(state.session_id, view.get("title", state.task.prompt))[:80] if state else "新会话")
                busy = view["task_worker"] is not None and not view["task_worker"].is_finished
                rows.append((f"live:{index}", f"{index + 1} · {'运行中' if busy else '空闲'} · {title} · {runtime.workspace.root}"))
                if state:
                    opened.add(state.session_id)
            rows.extend((row["id"], f"{row['id'][:12]} · {row['title']}")
                        for row in self.runtime.session_tree.sessions() if row["id"] not in opened)
            self.push_screen(SessionPicker(rows, title="会话 · 当前项目的历史与已打开的任务", query=argument), self._select_session)
        elif command == "/switch":
            args = shlex.split(argument)
            adopt = "--adopt-workspace" in args
            args = [arg for arg in args if arg != "--adopt-workspace"]
            if len(args) != 1:
                raise ValueError("用法：/switch 会话编号或序号 [--adopt-workspace]")
            if args[0].isdigit() and 1 <= int(args[0]) <= len(self._views):
                self._switch_view(self._views[int(args[0]) - 1])
            else:
                self._checkout_session(args[0], adopt_workspace=adopt)
        elif command == "/tree":
            if not self.runtime.state:
                raise ValueError("尚无会话，请先输入任务或恢复历史会话")
            rows, depths = [], {}
            for entry in self.runtime.session_tree.entries(self.runtime.state.session_id):
                depth = depths.get(entry["parent_id"], 0)
                depths[entry["id"]] = depth + int(entry["kind"] == "turn_start")
                if entry["restorable"]:
                    payload = entry["payload"]
                    marker = "●" if entry["id"] == self.runtime.session_tree.cursor else "◇"
                    rows.append((entry["id"], f"{'  ' * min(depth, 8)}{marker} {entry['id'][:12]} · {payload.get('phase')} · {payload.get('prompt', '')[:160]}"))
            self.push_screen(SessionPicker(rows, title="会话树 · ◇ 可恢复位置 · 输入新请求创建后续分支", query=argument, summaries=True), self._select_entry)
        elif command == "/branch":
            args = shlex.split(argument)
            flags = {arg for arg in args if arg.startswith("--")}
            ids = [arg for arg in args if not arg.startswith("--")]
            if len(ids) != 1 or flags - {"--summary", "--adopt-workspace"}:
                raise ValueError("用法：/branch 位置编号 [--summary] [--adopt-workspace]")
            self.runtime.checkout(ids[0], summary="--summary" in flags, adopt_workspace="--adopt-workspace" in flags)
            self._after_checkout()
        elif command == "/rename":
            if not self.runtime.state:
                raise ValueError("尚无会话")
            self.runtime.session_tree.rename(self.runtime.state.session_id, argument)
            self._view()["title"] = argument.strip()
            self._write("会话名称已保存。")
        elif command == "/entry":
            if not self.runtime.state:
                raise ValueError("尚无会话")
            entries = self.runtime.session_tree.entries(self.runtime.state.session_id, argument)
            if not argument or len(entries) != 1:
                raise ValueError("请输入唯一的记录编号")
            self._write(json.dumps(entries[0], ensure_ascii=False, indent=2))
        else:
            return False
        return True
