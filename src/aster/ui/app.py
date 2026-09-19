"""Chinese terminal conversation UI over the shared, cancellable Runtime."""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import suppress
from pathlib import Path

from ..config import Settings
from ..runtime import Runtime
from ..schema import TaskSpec
from .commands import (
    COMMANDS,
    PHASES,
    VERIFICATION,
    allowed_while_busy,
    execute_command,
    inspect_command,
    parse_command,
    saved_runs,
)

try:
    from rich.markdown import Markdown
    from rich.text import Text
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Vertical
    from textual.widgets import Input, RichLog, Static
    from textual.worker import WorkerCancelled, WorkerFailed

    from .widgets import HumanPrompt, PromptInput
except ImportError:  # pragma: no cover
    App = None


OUTCOMES = {
    "success": "完成",
    "error": "失败",
    "blocked": "已阻止",
    "timeout": "超时",
    "cancelled": "已取消",
}
ASTER_MARK = "\n".join(
    (
        " █████╗ ███████╗████████╗███████╗██████╗ ",
        "██╔══██╗██╔════╝╚══██╔══╝██╔════╝██╔══██╗",
        "███████║███████╗   ██║   █████╗  ██████╔╝",
        "██╔══██║╚════██║   ██║   ██╔══╝  ██╔══██╗",
        "██║  ██║███████║   ██║   ███████╗██║  ██║",
        "╚═╝  ╚═╝╚══════╝   ╚═╝   ╚══════╝╚═╝  ╚═╝",
    )
)


if App is not None:

    class AsterTui(App):
        TITLE = "Aster"
        ENABLE_COMMAND_PALETTE = False
        CSS = """
        Screen { layout: vertical; background: #0f1117; color: #e6e9f2; overflow: hidden; }
        #brand { height: 1; margin: 1 2 0 2; color: #a9b9ff; }
        #welcome { height: auto; margin: 1 2; padding: 1 2;
                   border: round #7165aa; background: #151822; }
        .compact #welcome { margin: 0 2; padding: 0 1; }
        #conversation { height: 1fr; min-height: 1; overflow: hidden; }
        #composer { height: auto; dock: bottom; }
        #log { height: 1fr; min-height: 1; padding: 0 2; background: #0f1117; }
        #log, #reasoning, #model-output { scrollbar-size: 1 1;
            scrollbar-color: #595176; scrollbar-background: #0f1117; }
        #reasoning, #model-output { height: auto; margin: 0 2; padding: 0 1;
                    border-left: thick #9b8afb; overflow-y: auto; }
        #reasoning { max-height: 25%; color: #b7afd7; }
        #model-output { max-height: 45%; border-left: thick #82aaff; }
        #progress { height: auto; max-height: 3; margin: 1 2 0 2; color: #a1abc0; }
        #suggestions { height: auto; max-height: 7; margin: 0 2;
                        padding: 0 1; background: #1c2030; }
        #prompt { margin: 0 2; border: round #596481; background: #151822; }
        #prompt:focus { border: round #9b8afb; }
        #hint { height: auto; max-height: 2; margin: 0 3; color: #7f8ba3; }
        """
        BINDINGS = [
            Binding("ctrl+c", "cancel_task", "取消任务", priority=True),
            Binding("ctrl+q", "quit", "退出", priority=True),
            Binding("ctrl+l", "clear_screen", "清屏", priority=True),
            Binding("f1", "help", "帮助"),
            Binding("escape", "escape", show=False),
        ]

        def __init__(self, root: Path, settings: Settings, *, max_steps=None, max_turns=None):
            super().__init__()
            self.root_path, self.settings = root.resolve(), settings
            self.task_limits = {
                key: value
                for key, value in {"max_steps": max_steps, "max_turns": max_turns}.items()
                if value is not None
            }
            self._initial_task = TaskSpec(prompt="交互会话", **self.task_limits)
            self.runtime = Runtime(
                root, settings, approval_callback=self._approve, question_callback=self._question
            )
            self.task_worker = None
            self._interaction_lock = asyncio.Lock()
            self._stream_text = self._reasoning_text = self._stream_status = (
                self._last_model_text
            ) = ""
            self._stream_active = False
            self._show_thinking = True
            self._activity = "就绪"
            self._started_at = 0.0
            self._mode = "work"
            self._context_report = None
            self._progress = None
            self._history = []
            self._history_index = 0
            self._draft = ""
            self._suggestions = []
            self._suggestion_index = 0

        @property
        def busy(self) -> bool:
            return self.task_worker is not None and not self.task_worker.is_finished

        def compose(self) -> ComposeResult:
            yield Static("", id="brand", markup=False)
            with Vertical(id="conversation"):
                yield Static("", id="welcome", markup=False)
                yield RichLog(id="log", wrap=True, markup=False, min_width=1, max_lines=6000)
                yield Static("", id="reasoning", markup=False)
                yield Static("", id="model-output", markup=False)
            with Vertical(id="composer"):
                yield Static("就绪", id="progress", markup=False)
                yield Static("", id="suggestions", markup=False)
                yield PromptInput(placeholder="输入任务，或输入 / 查看命令…", id="prompt")
                yield Static(
                    "Enter 发送 · / 帮助 · Tab 补全 · ↑↓ 历史 · Ctrl+C 取消 · Ctrl+Q 退出",
                    id="hint",
                    markup=False,
                )

        def on_mount(self) -> None:
            self.runtime.evidence.listeners.append(self._event)
            self._progress = self.query_one("#progress", Static)
            for name in ("model-output", "reasoning", "suggestions"):
                self.query_one(f"#{name}").display = False
            self._refresh_model()
            self._tick()
            self.set_interval(0.2, self._tick)
            self.query_one("#prompt", Input).focus()

        def _refresh_model(self) -> None:
            settings = self.settings
            self.sub_title = f"{settings.profile or '默认'} · {settings.model}"
            self.query_one("#brand", Static).update(
                Text(f"✦ Aster   /   {self.sub_title}", style="bold #b7b0ff")
            )
            mark = (
                ASTER_MARK
                if self.size.height >= 32 and self.size.width >= 55
                else "▄▀█ █▀ ▀█▀ █▀▀ █▀█\n█▀█ ▄█  █  ██▄ █▀▄"
            )
            welcome = Text(mark, style="bold #b7b0ff")
            welcome.append("\n\n你的终端编程伙伴\n", style="bold #e6e9f2")
            approval = {"ask": "操作前询问", "auto": "自动审批", "never": "不交互审批"}[
                settings.approval
            ]
            welcome.append(
                f"模型  {settings.model}    流式  {'开启' if settings.stream else '关闭'}\n",
                style="#a9b9ff",
            )
            welcome.append(self._budget_text() + "\n", style="#9ca7be")
            welcome.append(
                f"目录  {self.root_path}\n权限  {approval}    /help 查看帮助 · quit 退出",
                style="#9ca7be",
            )
            self.query_one("#welcome", Static).update(welcome)

        def _budget_text(self) -> str:
            settings = self.settings
            source = {
                "explicit": "已配置预算",
                "model_profile": "模型默认预算，含估算余量",
                "fallback": "未知模型，使用保守预算",
            }[settings.context_budget_source]
            return (
                f"上下文  {settings.effective_context_tokens - settings.effective_output_tokens:,} 输入"
                f" + {settings.effective_output_tokens:,} 输出 token（{source}）"
            )

        def on_resize(self) -> None:
            if self.is_mounted:
                self.screen.set_class(self.size.height < 32, "compact")
                # Keep the input reachable in short terminals.
                self.query_one("#welcome").display = self.size.height >= 20 and not self._history
                self._refresh_model()

        def _write(self, text: str, *, style: str = "") -> None:
            self.query_one("#log", RichLog).write(
                Text(self.runtime.evidence.redactor.text(text), style=style)
            )

        def _tick(self) -> None:
            if self._progress is None or not self._progress.is_mounted:
                return
            state = self.runtime.state
            task = state.task if state else self._initial_task
            elapsed = time.monotonic() - self._started_at if self.busy else 0
            lead = (
                f"{'⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏'[int(elapsed * 5) % 10]} {self._activity} · {elapsed:.1f}秒"
                if self.busy
                else self._activity
            )
            mode = state.mode if state else self._mode
            detail = f"{'规划' if mode == 'plan' else '执行'} · 轮次 {state.turn if state else 0}/{task.max_turns} · 工具 {state.step if state else 0}/{task.max_steps}"
            if state:
                detail += f" · 验证 {VERIFICATION[state.verification.state]}"
            self._progress.update(
                f"{lead}  │  {detail}" + (f"\n{self._stream_status}" if self._stream_status else "")
            )

        def _event(self, row: dict) -> None:
            event = row["event"]
            if event == "model_stream":
                self._stream_event(row)
            elif event == "context_built":
                self._context_report = row
                self._activity = "等待模型响应"
            elif event == "permission_decision":
                call = row["call"]
                args = call["arguments"]
                summary = args.get("command", args.get("path", args.get("pattern", "")))
                self._activity = f"执行 {call['name']}"
                self._write(f"  › {call['name']}  {str(summary)[:160]}", style="#a9b9ff")
            elif event == "tool_result":
                outcome = row["outcome"]
                self._write(
                    f"  {'✓' if outcome['status'] == 'success' else '✗'} {row['call']['name']} · {OUTCOMES.get(outcome['status'], outcome['status'])} {outcome.get('reason', '')}",
                    style="#a5c9a2" if outcome["status"] == "success" else "#efb38f",
                )
                if outcome.get("content"):
                    self._write(
                        "\n".join(outcome["content"].splitlines()[:8])[:1600], style="#8f9cb4"
                    )
                self._activity = "处理工具结果"
            elif event == "model_reply" and row.get("text"):
                self._last_model_text = row["text"]
                self._write("\n✦ Aster", style="bold #b7b0ff")
                self.query_one("#log", RichLog).write(
                    Markdown(self.runtime.evidence.redactor.text(row["text"]))
                )
            elif event == "worker_started":
                self._write(f"  › 子任务已启动：{row['worker_id']}")
            elif event == "provider_attempt" and row.get("classification") not in {
                "success",
                "cancelled",
            }:
                self._write(
                    f"  模型请求第 {row.get('attempt', 1)} 次：{row.get('classification')}，等待请求结束或重试。",
                    style="#efb38f",
                )
            self._tick()

        def _stream_event(self, row: dict) -> None:
            preview = self.query_one("#model-output", Static)
            reasoning = self.query_one("#reasoning", Static)
            kind, attempt = row["type"], row.get("attempt", 1)
            self._stream_status = f"模型请求 {attempt} · {row.get('elapsed_seconds', 0):.1f}秒"
            if kind == "begin":
                if self._stream_active:
                    self._write("上一次模型响应未完成，正在重新请求。", style="#efb38f")
                self._stream_text = self._reasoning_text = ""
                self._stream_active = True
                reasoning.display = False
                preview.display = True
                preview.update(f"等待模型响应（第 {attempt} 次请求）…")
                self._activity = "等待模型响应"
            elif kind == "text_delta":
                self._stream_text = (self._stream_text + row.get("text", ""))[-65536:]
                preview.display = True
                preview.update(self.runtime.evidence.redactor.text(self._stream_text))
                preview.scroll_end(animate=False)
                self._activity = "正在回答"
            elif kind == "reasoning_delta":
                self._reasoning_text = (self._reasoning_text + row.get("text", ""))[-32768:]
                reasoning.update(
                    "思考摘要\n" + self.runtime.evidence.redactor.text(self._reasoning_text)
                )
                reasoning.display = self._show_thinking
                reasoning.scroll_end(animate=False)
                self._activity = "正在思考"
            elif kind == "tool_delta":
                self._activity = "正在生成工具调用"
            elif kind == "completed":
                if self._reasoning_text and self._show_thinking:
                    self._write("思考摘要\n" + self._reasoning_text, style="#b7afd7")
                self._stream_active = False
                preview.display = reasoning.display = False
                self._stream_status = ""
            elif kind == "error":
                preview.update(
                    "模型响应未完成。\n" + self.runtime.evidence.redactor.text(self._stream_text)
                )
                self._activity = "等待重试" if row.get("retrying") else "响应中断"

        async def _prompt(self, screen: HumanPrompt) -> str | None:
            async with self._interaction_lock:
                previous = self._activity
                self._activity = "等待你的确认" if screen.approval else "等待你的回答"
                try:
                    return await self.push_screen_wait(screen)
                finally:
                    self._activity = previous
                    if self.screen is screen:
                        self.pop_screen()

        async def _approve(self, call, decision) -> bool:
            arguments = self.runtime.evidence.redactor.value(call.arguments)
            text = f"是否仅允许本次 {call.name} 操作？\n{decision.reason}\n{json.dumps(arguments, ensure_ascii=False, indent=2)}"
            return await self._prompt(HumanPrompt(text, approval=True)) == "yes"

        async def _question(self, question: str) -> str | None:
            return await self._prompt(HumanPrompt(question))

        def on_input_changed(self, event: Input.Changed) -> None:
            if event.input.id != "prompt":
                return
            text = event.value.strip()
            if text.startswith("/"):
                self.query_one("#welcome").display = False
            self._suggestions = (
                [name for name in COMMANDS if name.startswith(text)]
                if text.startswith("/") and " " not in event.value
                else []
            )
            self._suggestion_index = 0
            self._render_suggestions()

        def _render_suggestions(self) -> None:
            widget = self.query_one("#suggestions", Static)
            widget.display = bool(self._suggestions)
            start = max(0, self._suggestion_index - 5)
            text = Text()
            for index in range(start, min(start + 6, len(self._suggestions))):
                name = self._suggestions[index]
                selected = index == self._suggestion_index
                text.append(
                    f"{'›' if selected else ' '} {name}  {COMMANDS[name][1]}\n",
                    style="bold #c6bbff" if selected else "#939fba",
                )
            text.rstrip()
            widget.update(text)

        def complete_command(self) -> None:
            if self._suggestions:
                prompt = self.query_one("#prompt", Input)
                prompt.value = self._suggestions[self._suggestion_index] + " "
                prompt.cursor_position = len(prompt.value)

        def move_input(self, direction: int) -> None:
            if self._suggestions:
                self._suggestion_index = (self._suggestion_index + direction) % len(
                    self._suggestions
                )
                self._render_suggestions()
            elif self._history:
                prompt = self.query_one("#prompt", Input)
                if self._history_index == len(self._history):
                    self._draft = prompt.value
                self._history_index = max(
                    0, min(len(self._history), self._history_index + direction)
                )
                prompt.value = (
                    self._history[self._history_index]
                    if self._history_index < len(self._history)
                    else self._draft
                )
                prompt.cursor_position = len(prompt.value)

        def on_input_submitted(self, event: Input.Submitted) -> None:
            if event.input.id != "prompt":
                return
            event.stop()
            prompt = event.value.strip()
            if not prompt:
                return
            command, argument = parse_command(prompt)
            if command in {"/quit", "/cancel"} and not argument:
                event.input.value = ""
                self.run_worker(
                    self.action_quit() if command == "/quit" else self.action_cancel_task(),
                    exit_on_error=False,
                )
                return
            # Local inspection stays available during a request. Other input stays in the composer.
            if self.busy and not allowed_while_busy(command, argument):
                self._write(
                    "任务仍在运行；输入内容已保留。可用 /cancel 或 Ctrl+C 取消，quit 退出。",
                    style="#efb38f",
                )
                return
            event.input.value = ""
            self._history.append(prompt)
            self._history = self._history[-200:]
            self._history_index = len(self._history)
            self._draft = ""
            self.query_one("#welcome").display = False
            self._suggestions = []
            self._render_suggestions()
            if command.startswith("/"):
                try:
                    self._command(command, argument)
                except (OSError, ValueError, RuntimeError) as exc:
                    self._write(f"命令未执行：{exc}", style="#efb38f")
            else:
                self._start_task(prompt)

        def action_help(self) -> None:
            self.query_one("#welcome").display = False
            self._write("\n命令帮助", style="bold #b7b0ff")
            for name, (arguments, description) in COMMANDS.items():
                self._write(f"  {name} {arguments}\n    {description}")
            self._write(
                "\nEnter 发送 · Tab 补全 · ↑↓ 选择命令或浏览输入历史\nCtrl+C / Esc 取消任务 · Ctrl+L 清屏 · Ctrl+Q 退出\n普通输入沿当前会话继续；/review、/test 和带任务的 /plan 会调用模型。"
            )

        def _command(self, command: str, argument: str) -> None:
            execute_command(
                command, argument, self.runtime, write=self._write,
                show_help=self.action_help, clear_screen=self.action_clear_screen,
                model_command=self._model_command, new_session=self._new_session,
                start_task=self._start_task, reset_context=self._reset_context,
                configure_output=self._configure_output, configure_mode=self._configure_mode,
                inspect=self._inspect,
            )

        def _reset_context(self):
            self._context_report = None

        def _new_session(self):
            self.runtime.new_session()
            self._mode, self._context_report, self._activity = "work", None, "就绪"
            self.action_clear_screen()
            self._write("已开始新会话。请输入任务。")
            self._tick()

        def _saved_runs(self):
            return saved_runs(self.runtime.evidence)

        def _inspect(self, command: str):
            inspect_command(
                command, self.runtime, settings=self.settings, activity=self._activity,
                root_path=self.root_path, context_report=self._context_report,
                budget_text=self._budget_text(), write=self._write,
            )

        def _configure_output(self, command, argument):
            if argument not in {"", "on", "off"}:
                raise ValueError(f"用法：{command} [on|off]")
            if command == "/stream":
                if argument == "on" and self.settings.protocol != "responses":
                    raise ValueError("当前流式适配支持 Responses；此模型协议暂不支持。")
                if argument:
                    self.settings.stream = argument == "on"
                    self.runtime.checkpoint()
                    self._refresh_model()
                enabled = self.settings.stream
            else:
                if argument:
                    self._show_thinking = argument == "on"
                enabled = self._show_thinking
                self.query_one("#reasoning").display = (
                    enabled and self._stream_active and bool(self._reasoning_text)
                )
            self._write(
                f"{'流式输出' if command == '/stream' else '思考摘要'}：{'开启' if enabled else '关闭'}。"
                + ("仅显示接口实际返回的摘要。" if command == "/thinking" else "")
            )

        def _configure_mode(self, command, argument):
            state = self.runtime.state
            self._mode = "plan" if command == "/plan" else "work"
            if state:
                state.mode = self._mode
                self.runtime.checkpoint()
            self._write("已进入只读规划模式。" if self._mode == "plan" else "已返回执行模式。")
            if argument:
                self._start_task(argument)
            self._tick()

        def _model_command(self, argument: str) -> None:
            if not argument:
                self._write(
                    f"当前模型：{self.settings.profile or '默认'} · {self.settings.provider}/{self.settings.model} ({self.settings.protocol})"
                )
                if not self.settings.profiles:
                    self._write(
                        "尚未配置多个模型。可在 --config 指定的文件中添加 [profiles.名称]。"
                    )
                for alias, item in self.settings.profiles.items():
                    self._write(
                        f"  /model {alias} · {item.provider}/{item.model} · {item.protocol}"
                    )
                return
            self.settings = self.runtime.switch_model(argument)
            self._context_report = None
            self._refresh_model()
            self._write(f"当前会话已切换到 {self.settings.profile}。")

        def _start_task(self, prompt: str | None, *, adopt_workspace: bool = False) -> None:
            if prompt:
                self._write(f"\n› 你\n{prompt}", style="#c0ddc7")
            self._started_at, self._activity = time.monotonic(), "准备上下文"
            self.query_one(
                "#prompt", Input
            ).placeholder = "任务运行中 · /help 帮助 · /cancel 取消 · quit 退出"
            self.task_worker = self.run_worker(
                self._run_task(prompt, adopt_workspace=adopt_workspace), exit_on_error=False
            )

        async def _run_task(self, prompt: str | None, *, adopt_workspace: bool = False) -> None:
            self._last_model_text = self._stream_status = ""
            self._stream_active = False
            self.query_one("#model-output").display = self.query_one("#reasoning").display = False
            started = False
            try:
                if self.runtime.state:
                    self.runtime.continue_task(
                        prompt, adopt_workspace=adopt_workspace, **self.task_limits
                    )
                elif prompt is not None:
                    self.runtime.start(TaskSpec(prompt=prompt, **self.task_limits))
                    self.runtime.state.mode = self._mode
                else:
                    raise ValueError("没有可继续的会话，请先输入任务。")
                started = True
                state = await self.runtime.run()
                if not state.final or state.final != self._last_model_text:
                    self._write(state.final or f"任务已停止：{state.stop_reason}\n{state.feedback}")
            except asyncio.CancelledError:
                if self.runtime.state and not self.runtime.state.terminal:
                    self.runtime.abort()
                self._activity = "已取消"
                raise
            except Exception as exc:
                if started and self.runtime.state and not self.runtime.state.terminal:
                    self.runtime.finish("ui_error", failed=True)
                self._activity = "运行失败"
                self._write(f"运行失败：{type(exc).__name__}: {exc}", style="#efb38f")
            finally:
                if started and self.runtime.state:
                    state = self.runtime.state
                    self._activity = PHASES[state.phase]
                    self._write(
                        f"\n{PHASES[state.phase]} · 修改 {len(state.changed_paths)} 个文件 · 验证 {VERIFICATION[state.verification.state]}\n运行编号：{state.run_id}"
                        + (f" · 原因：{state.stop_reason}" if state.stop_reason else ""),
                        style="#8f9cb4",
                    )
                    if state.phase != "completed":
                        self._write(
                            f"输入 /continue 继续，或用 /resume {state.run_id} 恢复。已完成的修改会保留。"
                        )
                if self._stream_active:
                    self.query_one("#model-output", Static).update(
                        "模型响应未完成（已停止）。\n"
                        + self.runtime.evidence.redactor.text(self._stream_text)
                    )
                self._stream_status = ""
                self.query_one("#prompt", Input).placeholder = "输入任务，或输入 / 查看命令…"
                self.query_one("#prompt", Input).focus()

        def action_clear_screen(self) -> None:
            self.query_one("#log", RichLog).clear()
            self.query_one("#welcome").display = False
            if not self.busy:
                self.query_one("#model-output").display = self.query_one("#reasoning").display = (
                    False
                )

        async def action_escape(self) -> None:
            if self._suggestions:
                self._suggestions = []
                self._render_suggestions()
            elif self.busy:
                await self.action_cancel_task()

        async def action_cancel_task(self) -> None:
            if self.busy:
                self._activity = "正在取消"
                self.task_worker.cancel()
                with suppress(WorkerCancelled, WorkerFailed):
                    await self.task_worker.wait()
            else:
                self._write("当前没有运行中的任务。输入 quit 或按 Ctrl+Q 退出。")

        async def action_quit(self) -> None:
            if self.busy:
                await self.action_cancel_task()
            self.exit()

        async def on_unmount(self) -> None:
            if self._event in self.runtime.evidence.listeners:
                self.runtime.evidence.listeners.remove(self._event)
            if self.busy:
                await self.action_cancel_task()
            self.runtime.sandbox.close()
else:

    class AsterTui:
        def __init__(self, *_args, **_kwargs):
            raise RuntimeError("交互界面需要安装 textual")

        def run(self):
            raise RuntimeError("交互界面需要安装 textual")
