"""A small, inspectable control surface for explicit read-only delegation."""
from __future__ import annotations

from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, OptionList, RichLog, Static
from textual.widgets.option_list import Option

from ..schema import TaskSpec


class AgentPanel(ModalScreen):
    BINDINGS = [("escape", "close", "返回")]
    CSS = """
    AgentPanel { align: center middle; background: #0f1117 85%; }
    #agent-panel { width: 95%; height: 95%; border: round #9b8afb; padding: 0 1; background: #191c26; }
    #agent-title, #agent-notice { height: 1; }
    #agent-choices { height: 1fr; min-height: 2; }
    #agent-detail { height: 2fr; min-height: 2; }
    #agent-input { height: 3; }
    #agent-buttons { height: 3; }
    #agent-buttons Button { min-width: 4; width: auto; padding: 0 1; margin-right: 1; }
    """

    def __init__(self, runtime):
        super().__init__()
        self.runtime = runtime
        self.rows = []
        self._detail = ""
        self._acting = False
        self._events_key, self._events = None, []

    def compose(self):
        with Vertical(id="agent-panel"):
            yield Static("子任务 · 独立上下文 · 只读调查与审查", id="agent-title", markup=False)
            yield OptionList(id="agent-choices", markup=False)
            yield RichLog(id="agent-detail", wrap=True, markup=False, min_width=1)
            yield Static("新建调查，或选中子任务后发送补充说明。", id="agent-notice", markup=False)
            yield Input(placeholder="输入新任务或追问；Enter 向选中任务发送", id="agent-input")
            with Horizontal(id="agent-buttons"):
                yield Button("启动", id="agent-start", variant="primary")
                yield Button("发送", id="agent-send")
                yield Button("取消", id="agent-stop")
                yield Button("返回", id="agent-close")

    def on_mount(self):
        self.refresh_agents()
        self.set_interval(0.5, self.refresh_agents)
        self.query_one(Input).focus()

    def selected_id(self):
        choices = self.query_one(OptionList)
        return choices.get_option_at_index(choices.highlighted).id if choices.highlighted is not None else None

    @staticmethod
    def status_label(row):
        if row.get("stop_reason") in {"worker_cancelled", "cancelled"}:
            return "已中断"
        return {"running": "运行中", "ready": "运行中", "completed": "已完成",
                "failed": "失败", "stopped": "已中断", "cancelled": "已中断",
                "interrupted": "已中断", "unavailable": "快照不可用"}.get(row["status"], row["status"])

    def refresh_agents(self):
        rows = self.runtime.workers.overview()
        selected = self.selected_id()
        if rows != self.rows:
            self.rows = rows
            choices = self.query_one(OptionList)
            choices.clear_options().add_options(Option(self.runtime.evidence.redactor.text(
                f"{self.status_label(row)} · {row['worker_id']} · {' '.join(row['task'].split())[:160]}"
            ), id=row["worker_id"]) for row in rows)
            choices.highlighted = next((i for i, row in enumerate(rows) if row["worker_id"] == selected), 0) if rows else None
        self.query_one("#agent-send", Button).disabled = not rows or self._acting
        row = next((row for row in rows if row["worker_id"] == self.selected_id()), {})
        self.query_one("#agent-stop", Button).disabled = row.get("status") not in {"running", "ready"} or self._acting
        self.show_detail()

    def show_detail(self):
        row = next((row for row in self.rows if row["worker_id"] == self.selected_id()), None)
        text = "暂无子任务。输入明确的调查或审查任务后点击启动。"
        if row:
            text = f"{self.status_label(row)} · {row['task']}\n\n{row.get('answer') or '尚无最终结果。'}"
            if row.get("stop_reason"):
                text += "\n停止原因：" + row["stop_reason"]
            if row.get("reason") == "worker_snapshot_unavailable":
                text += "\n历史子任务快照不可用，请重新发起调查。"
            if row.get("stale_paths"):
                text += "\n文件已变化，采用结论前需重新核对：" + ", ".join(row["stale_paths"])
            if row.get("read_evidence"):
                text += "\n已读取文件：\n" + "\n".join(row["read_evidence"])
            key = (row.get("run_id"), row.get("steps"), row["status"], row.get("saved"))
            if key != self._events_key:
                self._events_key = key
                try:
                    events = self.runtime.evidence.events(row["run_id"]) if row.get("run_id") and not row.get("saved") else []
                    self._events = [f"{event['call']['name']} · {event['outcome']['status']}\n"
                                    + str(event['outcome'].get('content', ''))[:1200]
                                    for event in events if event["event"] == "tool_result"][-8:]
                except (OSError, ValueError):
                    self._events = []
            if self._events:
                text += "\n最近工具事件：\n" + "\n".join(self._events)
        text = self.runtime.evidence.redactor.text(text)
        if text != self._detail:
            self._detail = text
            self.query_one(RichLog).clear().write(text)

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted):
        event.stop()
        self.show_detail()

    def notice(self, text):
        if self.is_mounted:
            self.query_one("#agent-notice", Static).update(self.runtime.evidence.redactor.text(text))

    async def operate(self, action):
        if self._acting:
            return
        self._acting = True
        self.query_one("#agent-start", Button).disabled = True
        self.refresh_agents()
        try:
            prompt = self.query_one(Input)
            message = prompt.value.strip()
            worker_id = self.selected_id()
            if action != "stop" and not message:
                self.notice("请先输入任务或补充说明。")
                return
            if action == "start":
                if self.runtime.state is None:
                    self.runtime.start(TaskSpec(prompt="用户指定的并行调查：" + message,
                                               **self.app.task_limits, require_edit=False, require_verification=False))
                result = await self.runtime.workers.start(message, mode="explore", explicit=True)
            elif worker_id:
                result = (await self.runtime.workers.stop(worker_id) if action == "stop" else
                          await self.runtime.workers.message(worker_id, message, resume=True))
            else:
                self.notice("请先选择子任务。")
                return
            if result.get("status") in {"blocked", "missing"}:
                reason = result.get("reason", result["status"])
                self.notice("未执行：" + {
                    "worker_limit": "已达到子任务并行上限，请等待或取消一个任务。",
                    "team_budget_reserved_for_parent": "剩余预算不足，需为主任务整合保留额度。",
                    "worker_continuation_unavailable": "剩余预算不足，无法继续子任务。",
                    "worker_permissions_changed": "主会话权限已变化，无法继续原子任务。",
                    "worker_restore_blocked": "无法恢复，请核对历史记录与当前配置。",
                    "worker_snapshot_unavailable": "历史子任务快照不可用，请重新发起调查。",
                }.get(reason, reason))
            else:
                if action != "stop":
                    prompt.value = ""
                self.notice("已取消子任务。" if action == "stop" else "已发送；在列表中查看进展。")
        except (OSError, ValueError, RuntimeError) as exc:
            self.notice(f"未执行：{exc}")
        finally:
            self._acting = False
            if self.is_mounted:
                self.query_one("#agent-start", Button).disabled = False
                self.refresh_agents()

    def on_button_pressed(self, event: Button.Pressed):
        event.stop()
        action = event.button.id.removeprefix("agent-")
        if action == "close":
            self.action_close()
        else:
            self.run_worker(self.operate(action), exit_on_error=False)

    def on_input_submitted(self, event: Input.Submitted):
        event.stop()
        self.run_worker(self.operate("send" if self.selected_id() else "start"), exit_on_error=False)

    def action_close(self):
        self.dismiss(None)
