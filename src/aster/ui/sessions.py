"""Session navigation and independent live views for the terminal UI."""
from __future__ import annotations

import json
import shlex
from pathlib import Path

from rich.text import Text
from textual.widgets import Button, Input, OptionList, RichLog, Static
from textual.widgets.option_list import Option

from ..core.runtime import Runtime
from ..providers.client import ModelClient
from .session_widgets import SessionPicker, render_log, session_status
from .widgets import HumanPrompt

VIEW_FIELDS = (
    "runtime", "settings", "root_path", "task_worker", "_activity", "_started_at", "_mode",
    "_context_report", "_stream_text", "_reasoning_text", "_stream_status", "_last_model_text", "_stream_active",
)


class SessionViews:
    """UI mixin; each asynchronous run captures its own Runtime and event destination."""

    def _init_views(self):
        self._views = []
        self._live_rows = []
        self._sidebar_current = None
        self._register_view(self.runtime)

    def _register_view(self, runtime):
        view = {name: getattr(self, name) for name in VIEW_FIELDS}
        view.update(runtime=runtime, log=[], events=[], waiting="")
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
            view = self._view(runtime)
            if row["event"] == "model_stream":
                kind = row["type"]
                if kind == "begin":
                    view["_stream_text"] = view["_reasoning_text"] = ""
                    view["_stream_active"] = True
                elif kind in {"text_delta", "reasoning_delta"}:
                    field = "_stream_text" if kind == "text_delta" else "_reasoning_text"
                    view[field] = (view[field] + row.get("text", ""))[-65536:]
                elif kind == "completed":
                    view["_stream_active"] = False
                    if view["_reasoning_text"] and self._show_thinking:
                        view["events"].append({"event": "ui_note", "text": "思考摘要\n" + view["_reasoning_text"]})
                return
            view["events"].append(row)
            view["events"] = view["events"][-2000:]

    async def _session_approve(self, runtime, call, decision):
        arguments = runtime.evidence.redactor.value(call.arguments)
        text = f"会话 {runtime.state.session_id[:8]} · {runtime.workspace.root}\n是否仅允许本次 {call.name} 操作？\n{decision.reason}\n{json.dumps(arguments, ensure_ascii=False, indent=2)}"
        return await self._session_prompt(runtime, HumanPrompt(text, approval=True)) == "yes"

    async def _session_question(self, runtime, question):
        return await self._session_prompt(runtime, HumanPrompt(f"会话 {runtime.state.session_id[:8]} · {runtime.workspace.root}\n{question}"))

    async def _session_prompt(self, runtime, screen):
        view = self._view(runtime)
        view["waiting"] = "等待确认" if screen.approval else "等待回答"
        try:
            return await self._prompt(screen)
        finally:
            view["waiting"] = ""

    def _view_status(self, view):
        if view["waiting"]:
            return view["waiting"]
        worker = self.task_worker if view["runtime"] is self.runtime else view["task_worker"]
        if worker is not None and not worker.is_finished:
            return "工作中"
        state = view["runtime"].state
        return session_status(state.phase, state.stop_reason) if state else "就绪"

    def _view_title(self, view):
        runtime = view["runtime"]
        title = view.get("title") or (runtime.state.task.prompt if runtime.state else "新会话")
        return runtime.evidence.redactor.text(" ".join(title.split()))[:80]

    def _refresh_sessions(self):
        current = self._views.index(self._view())
        rows = [(f"live:{i}", f"{'●' if view['runtime'] is self.runtime else '○'} {i + 1} · {self._view_status(view)}\n{self._view_title(view)}")
                for i, view in enumerate(self._views)]
        if rows != self._live_rows:
            self._live_rows = rows
            choices = self._main_query("#live-sessions", OptionList)
            selected = choices.highlighted
            choices.clear_options().add_options(Option(label, id=key) for key, label in rows)
            choices.highlighted = current if current != self._sidebar_current else min(selected or 0, len(rows) - 1)
            self._sidebar_current = current
        statuses = [self._view_status(view) for view in self._views]
        self._main_query("#session-status", Static).update(Text(
            f"会话 {current + 1} · {self._view_status(self._view())} │ 工作 {statuses.count('工作中')} · 待确认 {sum(s.startswith('等待') for s in statuses)} · 完成 {statuses.count('已完成')}",
            style="#9ca7be",
        ))
        self._main_query("#cancel-session", Button).disabled = not self.busy
        self._main_query("#show-tree", Button).disabled = self.runtime.state is None

    def _session_rows(self):
        saved = self.runtime.session_tree.sessions()
        titles = {row["id"]: row["title"] for row in saved}
        rows, opened = [], set()
        for index, view in enumerate(self._views):
            runtime, state = view["runtime"], view["runtime"].state
            if state:
                # Historical names are scoped to their owning workspace database.
                if runtime.workspace.root == self.root_path and state.session_id in titles:
                    view["title"] = titles[state.session_id]
                opened.add(state.session_id)
            rows.append((f"live:{index}", f"{'●' if runtime is self.runtime else '○'} {index + 1} · {self._view_status(view)} · {self._view_title(view)}\n  {runtime.workspace.root}"))
        rows.extend((row["id"], f"{session_status(row['phase'], row['reason'])} · {row['title']} · {row['id'][:12]} · 历史")
                    for row in saved if row["id"] not in opened)
        return rows

    def action_session_list(self):
        self._session_command("/sessions", "")

    def action_session_tree(self):
        try:
            self._session_command("/tree", "")
        except (OSError, ValueError, RuntimeError) as exc:
            self.notify(str(exc), severity="warning")

    def action_branch_at(self, entry, summary=False):
        self._select_entry((entry, summary))

    def on_option_list_option_selected(self, event: OptionList.OptionSelected):
        if event.option_list.id == "live-sessions":
            event.stop()
            self._select_session((event.option.id, False))

    def on_button_pressed(self, event: Button.Pressed):
        event.stop()
        actions = {"show-sessions": self.action_session_list, "show-tree": self.action_session_tree,
                   "show-help": self.action_help}
        if event.button.id in actions:
            actions[event.button.id]()
        elif event.button.id == "new-session":
            self.push_screen(HumanPrompt(
                "新会话的工作目录。同一目录的任务依次执行；不同目录或 worktree 可并行工作。",
                value=str(self.root_path), accept_label="打开会话",
            ), self._select_new_session)
        elif event.button.id == "cancel-session":
            self.run_worker(self.action_cancel_task(), exit_on_error=False)

    def _select_new_session(self, root):
        if root:
            try:
                self._open_session(root)
            except (OSError, ValueError, RuntimeError) as exc:
                self.notify(f"未打开：{exc}", severity="error")

    def _switch_view(self, view):
        self._save_view()
        for name in VIEW_FIELDS:
            setattr(self, name, view[name])
        self._redraw_transcript()
        events, view["events"] = view["events"], []
        for event in events:
            self._event(event)
        self._main_query("#prompt", Input).value = view.get("draft", "")
        self._main_query("#model-output", Static).update(self._stream_text)
        self._main_query("#model-output").display = self._stream_active and self.busy
        self._main_query("#reasoning", Static).update("思考摘要\n" + self._reasoning_text)
        self._main_query("#reasoning").display = self._show_thinking and self._stream_active and bool(self._reasoning_text)
        self._refresh_model()
        self._tick()
        self._main_query("#welcome").display = not view["log"] and not self.runtime.state
        self._main_query("#prompt", Input).focus()

    def _redraw_transcript(self, preserve_scroll=False):
        if not self.is_running or self._sessions_closing:
            return
        log = self._main_query("#log", RichLog)
        position, at_end = log.scroll_y, log.is_vertical_scroll_end
        log.clear()
        for text, style in self._view()["log"]:
            log.write(render_log(text, style))
        if preserve_scroll and not at_end:
            log.scroll_to(y=position, animate=False)

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
        self._main_query("#prompt", Input).value = ""
        self.action_clear_screen()
        self._refresh_model()
        self._write(f"已打开独立会话 · {target}\n输入任务开始；点击「会话」切换。")
        self._tick()
        self._main_query("#prompt", Input).focus()

    def _select_entry(self, result, *, adopt_workspace=False):
        if result is None:
            return
        entry, summary = result
        try:
            if self.busy:
                raise RuntimeError("当前会话正在工作，请等待完成或点击「取消」后选择分支。")
            self.runtime.checkout(entry, summary=summary, adopt_workspace=adopt_workspace)
            self._after_checkout()
        except (OSError, ValueError, RuntimeError) as exc:
            if str(exc) == "workspace_mismatch" and not adopt_workspace:
                self._confirm_workspace(lambda: self._select_entry(result, adopt_workspace=True))
            else:
                self.notify(f"未切换：{exc}", severity="warning")

    def _confirm_workspace(self, proceed):
        origin = self.runtime
        def selected(answer):
            if answer == "yes":
                if self.runtime is origin:
                    proceed()
                else:
                    self.notify("前台会话已改变，请重新选择目标位置。", severity="warning")
        self.push_screen(HumanPrompt(
            "当前文件与历史位置不同。分支只恢复对话上下文，不回滚文件。\n"
            "是否保留当前文件并继续？旧文件读取记录和验证结果会失效。",
            approval=True, accept_label="保留文件并继续",
        ), selected)

    def _dialogue_footer(self, runtime):
        entry = runtime.session_tree.cursor
        if entry:
            self._session_write(runtime, f"◇ 对话位置 {entry[:8]}", style=f"branch:{entry}")

    def _render_session_history(self):
        tree = self.runtime.session_tree
        entries = {row["id"]: row for row in tree.entries(self.runtime.state.session_id)}
        path, cursor = [], tree.cursor
        while cursor in entries:
            row = entries[cursor]
            path.append(row)
            cursor = row["parent_id"]
        path.reverse()
        endings = {}
        for row in path:
            if row["restorable"] and row["payload"].get("phase") in {"completed", "stopped", "failed"} and row["payload"].get("reason") != "branch_selected":
                endings[row["run_id"]] = row["id"]
        prompt = None
        for row in path:
            kind, payload = row["kind"], row["payload"]
            if kind == "turn_start" and payload.get("prompt"):
                prompt = payload["prompt"]
                self._write("\n› 你\n" + prompt, style="#c0ddc7")
            elif kind == "message":
                message = payload["message"]
                content = message.get("content", "")
                if isinstance(content, list):
                    content = "\n".join(block.get("text", "") for block in content if isinstance(block, dict))
                if message.get("role") in {"user", "assistant"} and content:
                    if message["role"] == "user" and content == prompt:
                        prompt = None
                        continue
                    self._write(f"{'你' if message['role'] == 'user' else 'Aster'}：{content}")
            elif kind == "compaction":
                self._write("· 此处压缩了模型上下文；上方仍可查看原对话。", style="#8f9cb4")
            if endings.get(row["run_id"]) == row["id"]:
                self._write(f"◇ 对话位置 {row['id'][:8]}", style=f"branch:{row['id']}")

    def _after_checkout(self):
        self.settings = self.runtime.settings
        self._mode = self.runtime.state.mode
        self._context_report = None
        self._activity = "已选择历史位置"
        self.action_clear_screen()
        self._render_session_history()
        self._write(f"会话 {self.runtime.state.session_id[:12]} · 位置 {self.runtime.session_tree.cursor[:12]}\n历史和模型已恢复，文件保持当前状态。输入新请求会从此处继续。")
        self._refresh_model()
        self._tick()
        self._main_query("#prompt", Input).focus()

    def _select_session(self, result, *, adopt_workspace=False):
        if result is None:
            return
        reference = result[0]
        try:
            if reference.startswith("live:"):
                self._switch_view(self._views[int(reference[5:])])
            else:
                self._checkout_session(reference, adopt_workspace=adopt_workspace)
        except (ValueError, RuntimeError, OSError) as exc:
            if str(exc) == "workspace_mismatch" and not adopt_workspace:
                self._confirm_workspace(lambda: self._select_session(result, adopt_workspace=True))
            else:
                self.notify(f"未切换：{exc}", severity="warning")

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
        saved = restored.session_tree.sessions(restored.state.session_id)
        if saved:
            self._view()["title"] = saved[0]["title"]
        self._after_checkout()

    def _session_command(self, command, argument):
        if command == "/new":
            args = shlex.split(argument)
            if len(args) > 1:
                raise ValueError('用法：/new ["工作目录"]')
            self._open_session(args[0] if args else None)
        elif command == "/sessions":
            self._save_view()
            self.push_screen(SessionPicker(self._session_rows(), title="会话 · 实时状态与项目历史", query=argument,
                                           refresh_rows=self._session_rows), self._select_session)
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
            rows, depths, boundaries = [], {}, {}
            labels = {"model_change": "模型配置", "compaction": "上下文压缩", "context_transform": "模型历史转换", "branch_summary": "分支摘录", "branch": "分支选择", "turn_start": "用户请求"}
            changes = []
            for entry in self.runtime.session_tree.entries(self.runtime.state.session_id):
                depth = depths.get(entry["parent_id"], 0)
                depths[entry["id"]] = depth + int(entry["kind"] == "turn_start")
                boundary = boundaries.get(entry["parent_id"])
                boundaries[entry["id"]] = entry["id"] if entry["restorable"] else boundary
                if entry["kind"] in labels:
                    changes.append(f"{labels[entry['kind']]} {entry['id'][:8]}")
                if entry["restorable"]:
                    payload = entry["payload"]
                    marker = "●" if entry["id"] == self.runtime.session_tree.cursor else "◇"
                    phase = session_status(payload.get("phase"), payload.get("reason"))
                    parent = f" ← {boundary[:8]}" if boundary else " · 起点"
                    rows.append((entry["id"], f"{'  ' * min(depth, 8)}{marker} {entry['id'][:12]}{parent} · {phase} · {payload.get('prompt', '')[:160]}"
                                 + ("\n  " + " / ".join(changes) if changes else "")))
                    changes.clear()
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
