"""Small injectable lifecycle hooks; hook failures do not widen authority."""

from collections.abc import Iterable
from dataclasses import dataclass, field

from .schema import Outcome, RunState, ToolCall


@dataclass(frozen=True)
class HookResult:
    action: str = "continue"
    feedback: str = ""
    metadata: dict = field(default_factory=dict)


class Hooks:
    def __init__(self, plugins: Iterable[object] = ()):
        self.plugins = list(plugins)

    def add(self, plugin: object) -> None:
        self.plugins.append(plugin)

    @staticmethod
    def _safe(plugin, name, *args) -> HookResult:
        callback = getattr(plugin, name, None)
        if callback is None:
            return HookResult()
        try:
            result = callback(*args)
            return result if isinstance(result, HookResult) else HookResult()
        except Exception as exc:
            # A failed precondition must not silently authorize a tool.
            return HookResult(
                "deny" if name == "before_tool" else "continue",
                f"hook_error:{name}:{type(exc).__name__}",
            )

    def before_tool(self, state: RunState, call: ToolCall) -> HookResult:
        if call.call_id in state.used_call_ids:
            return HookResult("deny", "duplicate_call_id")
        for plugin in self.plugins:
            result = self._safe(plugin, "before_tool", state, call)
            if result.action in {"deny", "stop"}:
                return result
        return HookResult()

    def after_tool(self, state: RunState, call: ToolCall, outcome: Outcome) -> HookResult:
        return self._run("after_tool", state, call, outcome)

    def after_failure(self, state: RunState, call: ToolCall, outcome: Outcome) -> HookResult:
        return self._run("after_failure", state, call, outcome)

    def stop(self, state: RunState) -> HookResult:
        return self._run("stop", state)

    def _run(self, name, *args) -> HookResult:
        feedback, metadata = [], {}
        for plugin in self.plugins:
            result = self._safe(plugin, name, *args)
            if result.feedback:
                feedback.append(result.feedback)
            metadata.update(result.metadata)
            if result.action == "stop":
                return HookResult("stop", "\n".join(feedback), metadata)
        return HookResult("continue", "\n".join(feedback), metadata)
