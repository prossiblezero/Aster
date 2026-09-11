"""Small fixed lifecycle hooks; hook failures never widen authority."""

from dataclasses import dataclass, field

from .schema import Outcome, RunState, ToolCall


@dataclass(frozen=True)
class HookResult:
    action: str = "continue"
    feedback: str = ""
    metadata: dict = field(default_factory=dict)


class Hooks:
    def before_tool(self, state: RunState, call: ToolCall) -> HookResult:
        if call.call_id in state.used_call_ids:
            return HookResult("deny", "duplicate_call_id")
        return HookResult()

    def after_tool(self, state: RunState, call: ToolCall, outcome: Outcome) -> HookResult:
        return HookResult()

    def after_failure(self, state: RunState, call: ToolCall, outcome: Outcome) -> HookResult:
        return HookResult()

    def stop(self, state: RunState) -> HookResult:
        return HookResult()
