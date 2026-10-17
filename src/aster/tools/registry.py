"""Validate direct calls or dispatch the exact arguments already preflighted by Runtime."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import BaseModel

from ..safety.runner import SandboxRunner
from ..schema import Outcome, ToolCall
from ..storage.workspace import Workspace
from .schemas import SCHEMAS, WORKFLOW_TOOLS
from .workflow import WorkflowTools
from .workspace import WorkspaceTools

if TYPE_CHECKING:
    from .mcp import MCPTools


def validate_arguments(call: ToolCall, *, direct: bool = False, schemas=None) -> BaseModel | Outcome:
    schemas = SCHEMAS if schemas is None else schemas
    if call.name not in schemas:
        return Outcome(status="blocked", reason="unknown_tool",
                       content=f"unknown tool {call.name}" if direct else "")
    try:
        return schemas[call.name][0].model_validate(call.arguments)
    except ValueError as exc:
        return Outcome(status="blocked", reason="invalid_arguments", content=str(exc))


@dataclass
class ToolContext:
    workspace: Workspace
    sandbox: SandboxRunner
    run_id: str
    expected_files: dict[str, str | None] | None = None
    readonly: bool = False
    workflow: WorkflowTools | None = None
    mcp: MCPTools | None = None
    # Original task text and already-checked failure signatures for run_tests.
    baseline_text: dict[str, str] | None = None
    baseline_signatures: dict[str, str] | None = None


class ToolRegistry:
    def __init__(self, context: ToolContext, *, validated: tuple[ToolCall, BaseModel] | None = None):
        self.workspace = WorkspaceTools(context)
        self.workflow = context.workflow
        self.mcp = context.mcp
        self._validated = (validated[0].model_copy(deep=True), validated[1]) if validated else None

    async def execute(self, call: ToolCall) -> Outcome:
        args = (self._validated[1] if self._validated and call == self._validated[0]
                else validate_arguments(call, direct=True,
                                        schemas=self.mcp.schemas if self.mcp else None))
        if isinstance(args, Outcome):
            return args
        try:
            if self.mcp and (call.name == "mcp_list" or call.name in self.mcp.tools):
                return await self.mcp.execute(call.model_copy(update={"arguments": args.model_dump()}))
            if call.name in WORKFLOW_TOOLS:
                if self.workflow is None:
                    return Outcome(status="blocked", reason="workflow_not_implemented", content=call.name)
                return await self.workflow.execute(call.name, args.model_dump())
            return await self.workspace.execute_validated(call, args)
        except Exception as exc:
            return Outcome(status="error", reason=type(exc).__name__, content=str(exc))
