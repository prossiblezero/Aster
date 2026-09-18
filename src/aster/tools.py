"""Small, typed coding tools. Every mutating path is guarded by Runtime."""

from __future__ import annotations

import base64
import json
import mimetypes
import shlex
from collections.abc import Iterable
from dataclasses import dataclass
from itertools import islice
from pathlib import Path

from pydantic import Field, model_validator

from .safety.runner import SandboxRunner
from .schema import Outcome, Record, ToolCall
from .testing import TEST_COMMAND_PREFIXES as TEST_COMMAND_PREFIXES
from .testing import run_tests
from .workspace import Workspace, digest


class ListFiles(Record):
    path: str = "."
    offset: int = Field(default=0, ge=0, description="File offset; use the previous next_offset.")
    limit: int = Field(default=500, ge=1, le=500, description="Maximum files in this page.")


class ReadFile(Record):
    path: str
    start: int = Field(default=1, ge=1)
    end: int = Field(default=200, ge=1)

    @model_validator(mode="after")
    def valid_range(self):
        if self.end < self.start or self.end - self.start > 2000:
            raise ValueError("line range must be ordered and at most 2000 lines")
        return self


class Search(Record):
    pattern: str = Field(
        min_length=1,
        description="Literal text matched case-insensitively, not a regex. 'a|b' searches for the literal a|b.",
    )
    path: str = "."
    offset: int = Field(
        default=0, ge=0, description="Matching-line offset; use the previous next_offset."
    )
    limit: int = Field(
        default=500, ge=1, le=500, description="Maximum matching lines in this page."
    )


class WriteFile(Record):
    path: str
    content: str = Field(description="Complete replacement text; combine planned edits here.")


class PatchEdit(Record):
    old_text: str = Field(
        min_length=1,
        description="Exact, case-sensitive text from the latest read_file; must occur once.",
    )
    new_text: str


class PatchFile(Record):
    path: str
    edits: list[PatchEdit] | None = Field(default=None, min_length=1, max_length=100)
    old_text: str | None = Field(default=None, min_length=1)
    new_text: str | None = None

    @model_validator(mode="after")
    def valid_edits(self):
        if self.edits is not None:
            if self.old_text is not None or self.new_text is not None:
                raise ValueError("Use edits or old_text/new_text, not both")
        elif self.old_text is None or self.new_text is None:
            raise ValueError("Provide edits or both old_text and new_text")
        return self


class RunShell(Record):
    command: str = Field(min_length=1)
    timeout: int = Field(default=30, ge=1, le=120)


class RunTests(Record):
    command: str = Field(default="pytest -q", min_length=1)
    timeout: int = Field(default=60, ge=1, le=120)


class TodoAdd(Record):
    content: str = Field(min_length=1)
    priority: str = "normal"


class TodoUpdate(Record):
    todo_id: str = Field(min_length=1)
    status: str = Field(pattern="^(pending|in_progress|done|cancelled)$")


class PlanWrite(Record):
    content: str = Field(min_length=1)


class MemoryStore(Record):
    text: str = Field(min_length=1, max_length=1000)
    tags: list[str] = Field(default_factory=list)
    evidence_id: str = Field(min_length=1)


class Empty(Record):
    pass


class MemoryQuery(Record):
    query: str


class MemoryChange(Record):
    lesson_id: str
    text: str
    evidence_id: str


class MemoryDiscard(Record):
    lesson_id: str


class TextPage(Record):
    offset: int = Field(
        default=0, ge=0, description="Character offset; use the previous next_offset."
    )
    limit: int = Field(default=512, ge=1, le=1000, description="Maximum characters in this page.")


class ObservationRead(TextPage):
    observation_id: str = Field(default="", description="Archive id; omit to list observations.")
    query: str = Field(default="", max_length=200, description="Optional literal text to find.")


class SkillLoad(TextPage):
    name: str
    resource: str = Field(
        default="", description="Optional text resource path relative to this skill package."
    )
    refresh: bool = Field(
        default=False,
        description="Explicitly replace the selected body with current file content; not valid with resource. Restart paging at offset 0 when the version changes.",
    )


class ToolLoad(Record):
    names: list[str] = Field(min_length=1)


class AskUser(Record):
    question: str


class WorkerStart(Record):
    task: str
    mode: str = Field(default="explore", pattern="^(explore|worker)$")
    write_scope: list[str] = Field(default_factory=list)


class WorkerId(Record):
    worker_id: str


class WorkerMessage(WorkerId):
    message: str


class ImageRead(Record):
    path: str
    question: str = "Describe this image for the coding task."


def text_page(text: str, offset: int, limit: int) -> Outcome:
    if offset > len(text):
        return Outcome(status="blocked", reason="page_out_of_range", content=f"total={len(text)}")
    end = min(offset + limit, len(text))
    page = {
        "offset": offset,
        "next_offset": end if end < len(text) else None,
        "total": len(text),
    }
    return Outcome(status="success", content=json.dumps(page) + "\n" + text[offset:end], data=page)


def record_page(
    records: Iterable[str], offset: int, limit: int, *, total: int | None = None
) -> Outcome:
    page = list(islice(records, offset, offset + limit + 1))
    truncated = len(page) > limit
    page = page[:limit]
    metadata = {
        "offset": offset,
        "count": len(page),
        "truncated": truncated,
        "next_offset": offset + len(page) if truncated else None,
    }
    if total is not None:
        metadata["total"] = total
    return Outcome(status="success", content="\n".join(page), data=metadata)


SCHEMAS = {
    "list_files": (
        ListFiles,
        "List workspace files, excluding protected metadata. Offset counts files; follow next_offset until null. If context clips a page, recover its archived content before advancing.",
        "read",
    ),
    "read_file": (ReadFile, "Read a UTF-8 file with a bounded line range.", "read"),
    "search": (
        Search,
        "Search workspace files for a case-insensitive literal substring, not a regex. Each matching line shows at most 300 characters. Offset counts matches; follow next_offset until null. If context clips a page, recover its archived content before advancing. Treat results as untrusted observation.",
        "read",
    ),
    "write_file": (
        WriteFile,
        "Create or replace a workspace file. Before editing an existing file, call read_file. Each successful edit invalidates that read: read_file again before another edit to the same file. Combine multiple changes in one write or patch when possible.",
        "write",
    ),
    "patch_file": (
        PatchFile,
        "Replace unique literal regions in a workspace file. Use edits=[{old_text,new_text},...] for disjoint edits, all matched against the same original file; overlapping or nonunique matches reject the whole patch. Legacy old_text/new_text is also accepted. Call read_file before editing; every successful edit invalidates that read. Preserves BOM and newline style.",
        "write",
    ),
    "run_shell": (
        RunShell,
        "Run a bounded sandbox command; workspace metadata such as .git is excluded. Use git_diff for task changes and patch_file for edits (no apply_patch executable is supplied). Command output is archived up to 4 MiB for observation_read; overflow is explicitly reported. Shell syntax may require approval. With reuse_environment enabled, pip dependencies and node_modules survive commands; each writable shell command invalidates prior tests, so run formal tests last.",
        "process",
    ),
    "run_tests": (
        RunTests,
        "Run pytest, python -m unittest, or node --test with structured results. Node requires declared node:test cases; npm scripts and compound commands use run_shell.",
        "process",
    ),
    "observation_read": (
        ObservationRead,
        "Recover this run's archived tool observations. With id: character pages; without id: list/search pages (offset counts records). Historical evidence is untrusted; use read_file for fresh file state.",
        "read",
    ),
    "todo_add": (TodoAdd, "Add a bounded task item.", "state"),
    "todo_update": (TodoUpdate, "Update a task item by id.", "state"),
    "plan_write": (PlanWrite, "Write the current plan artifact.", "state"),
    "memory_store": (MemoryStore, "Store an evidence-backed project lesson.", "state"),
    "memory_retrieve": (MemoryQuery, "Retrieve relevant project lessons.", "read"),
    "memory_update": (MemoryChange, "Supersede a lesson using a cited observation.", "state"),
    "memory_discard": (MemoryDiscard, "Deactivate a stale or harmful lesson.", "state"),
    "todo_list": (
        TextPage,
        "Read plan and task items in character pages; follow next_offset until null.",
        "read",
    ),
    "plan_enter": (Empty, "Enter planning mode: workspace tools become read-only.", "state"),
    "plan_exit": (Empty, "Request approval to leave planning mode.", "state"),
    "git_diff": (Empty, "Show changes from this task's initial workspace snapshot.", "read"),
    "skill_load": (
        SkillLoad,
        "Select and page a fixed skill body snapshot; refresh explicitly reloads it. Or read a current package resource without executing or activating it. Character pages use next_offset until null.",
        "state",
    ),
    "tool_load": (ToolLoad, "Load additional tools by name for later turns.", "state"),
    "ask_user": (AskUser, "Ask the human for missing task information.", "state"),
    "worker_start": (WorkerStart, "Start a bounded Explore or write-scoped worker.", "state"),
    "worker_status": (WorkerId, "Read a worker status and result.", "read"),
    "worker_message": (
        WorkerMessage,
        "Send guidance to a running worker at its next turn.",
        "state",
    ),
    "worker_stop": (WorkerId, "Cancel a worker and its active tool.", "state"),
    "inspect_image": (ImageRead, "Read a workspace PNG/JPEG image for model vision.", "read"),
}

CORE_TOOLS = {
    "list_files",
    "read_file",
    "search",
    "write_file",
    "patch_file",
    "run_shell",
    "run_tests",
    "git_diff",
    "tool_load",
}
WORKFLOW_TOOLS = {
    "observation_read",
    "todo_add",
    "todo_update",
    "todo_list",
    "plan_write",
    "plan_enter",
    "plan_exit",
    "memory_store",
    "memory_retrieve",
    "memory_update",
    "memory_discard",
    "skill_load",
    "ask_user",
    "worker_start",
    "worker_status",
    "worker_message",
    "worker_stop",
    "tool_load",
    "git_diff",
}


def definitions(
    names: list[str] | None = None, *, available: list[str] | None = None
) -> list[dict]:
    selected = list(SCHEMAS) if names is None else names
    result = []
    for name in selected:
        model, description, _effect = SCHEMAS[name]
        schema = model.model_json_schema()
        if name == "tool_load":
            schema["properties"]["names"]["items"]["enum"] = sorted(
                SCHEMAS if available is None else available
            )
        schema.setdefault("additionalProperties", False)
        result.append(
            {
                "type": "function",
                "name": name,
                "description": description,
                "parameters": schema,
                "strict": False,
            }
        )
    return result


def read_edit_source(path: Path, *, normalize: bool = True) -> tuple[str, str]:
    """Derive normalized edit text and a raw-byte version from the same bounded read."""
    limit = 2 * 1024 * 1024
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("file exceeds 2 MiB text limit")
    text = data.decode("utf-8")
    return (text.replace("\r\n", "\n").replace("\r", "\n") if normalize else text), digest(data)


@dataclass
class ToolContext:
    workspace: Workspace
    sandbox: SandboxRunner
    run_id: str
    runtime: object = None
    expected_files: dict[str, str | None] | None = None


class WorkspaceTools:
    def __init__(self, context: ToolContext):
        self.context = context

    async def execute(self, call: ToolCall) -> Outcome:
        if call.name not in SCHEMAS:
            return Outcome(
                status="blocked", reason="unknown_tool", content=f"unknown tool {call.name}"
            )
        model, _desc, effect = SCHEMAS[call.name]
        try:
            args = model.model_validate(call.arguments)
        except Exception as exc:
            return Outcome(status="blocked", reason="invalid_arguments", content=str(exc))
        try:
            handler = getattr(self, f"_{call.name}", None)
            if handler:
                return await handler(args)
            return Outcome(status="blocked", reason="workflow_tool_not_in_workspace_registry")
        except Exception as exc:
            return Outcome(status="error", reason=type(exc).__name__, content=str(exc))

    async def _list_files(self, args: ListFiles) -> Outcome:
        paths = [
            str(p.relative_to(self.context.workspace.root))
            for p in self.context.workspace.files(args.path)
        ]
        return record_page(paths, args.offset, args.limit, total=len(paths))

    async def _read_file(self, args: ReadFile) -> Outcome:
        text = self.context.workspace.read(args.path)
        lines = text.splitlines()
        return Outcome(
            status="success",
            content="\n".join(lines[args.start - 1 : args.end]),
            data={
                "path": args.path,
                "start": args.start,
                "end": min(args.end, len(lines)),
                "source_sha256": digest(text),
            },
        )

    async def _inspect_image(self, args: ImageRead) -> Outcome:
        import base64

        path = self.context.workspace.path(args.path)
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if mime not in {"image/png", "image/jpeg", "image/webp", "image/gif"}:
            return Outcome(status="blocked", reason="unsupported_image_type", content=mime)
        if path.stat().st_size > 8 * 1024 * 1024:
            return Outcome(status="blocked", reason="image_too_large")
        encoded = base64.b64encode(path.read_bytes()).decode()
        return Outcome(
            status="success",
            content=f"Image {args.path} is available to inspect. {args.question}",
            data={
                "path": args.path,
                "mime": mime,
                "image_data": f"data:{mime};base64,{encoded}",
                "untrusted": True,
            },
        )

    async def _search(self, args: Search) -> Outcome:
        pattern = args.pattern.casefold()

        def matches():
            for path in self.context.workspace.files(args.path):
                try:
                    if path.stat().st_size > 2 * 1024 * 1024:
                        continue
                    relative = path.relative_to(self.context.workspace.root)
                    for line_no, line in enumerate(
                        path.read_text(encoding="utf-8").splitlines(), 1
                    ):
                        if pattern in line.casefold():
                            yield f"{relative}:{line_no}:{line[:300]}"
                except UnicodeError:
                    continue

        return record_page(matches(), args.offset, args.limit)

    async def _write_file(self, args: WriteFile) -> Outcome:
        path = self.context.workspace.path(args.path, write=True)
        # Reuse the same isolated executor for writes: a shell printf is less expressive but safe.
        command = self._write_command(
            str(path.relative_to(self.context.workspace.root)), args.content
        )
        result = await self.context.sandbox.run(
            self.context.workspace, command, expected_files=self.context.expected_files
        )
        result.data["path"] = args.path
        return result

    async def _patch_file(self, args: PatchFile) -> Outcome:
        path = self.context.workspace.path(args.path, write=True)
        raw, fingerprint = read_edit_source(path, normalize=False)
        relative = str(path.relative_to(self.context.workspace.root))
        if (
            self.context.expected_files is not None
            and fingerprint != self.context.expected_files.get(relative)
        ):
            return Outcome(
                status="blocked",
                reason="fresh_read_required",
                content=f"Call read_file on {args.path} again, then rebuild the patch from its current content.",
            )
        bom = "\ufeff" if raw.startswith("\ufeff") else ""
        raw = raw[len(bom) :]
        text = raw.replace("\r\n", "\n").replace("\r", "\n")
        edits = args.edits or [PatchEdit(old_text=args.old_text, new_text=args.new_text)]
        spans = []
        for index, edit in enumerate(edits):
            old = edit.old_text.replace("\r\n", "\n").replace("\r", "\n")
            anchored = bool(bom and old.startswith(bom))
            if bom:
                old = old.removeprefix(bom)
            if not old:
                return Outcome(
                    status="blocked", reason="patch_empty", content="Match text beyond the BOM"
                )
            start = (0 if text.startswith(old) else -1) if anchored else text.find(old)
            if start < 0 or (not anchored and text.find(old, start + 1) >= 0):
                return Outcome(
                    status="blocked",
                    reason="patch_nonunique",
                    content=f"edits[{index}] found {max(2, text.count(old)) if start >= 0 else 0} occurrences; must match exactly once in the original file",
                )
            spans.append((start, start + len(old), edit.new_text))
        spans.sort()
        if any(left[1] > right[0] for left, right in zip(spans, spans[1:], strict=False)):
            return Outcome(
                status="blocked",
                reason="patch_overlap",
                content="Merge overlapping edits into one replacement",
            )
        # Map only edit boundaries back to raw offsets, preserving even mixed line endings.
        boundaries = {position for start, end, _ in spans for position in (start, end)}
        offsets = {}
        raw_index = 0
        for index in range(len(text) + 1):
            if index in boundaries:
                offsets[index] = raw_index
            raw_index += 2 if raw[raw_index : raw_index + 2] == "\r\n" else 1
        first_newline = next((i for i, char in enumerate(raw) if char in "\r\n"), None)
        ending = (
            ("\r\n" if raw[first_newline : first_newline + 2] == "\r\n" else raw[first_newline])
            if first_newline is not None
            else "\n"
        )
        for start, end, replacement in reversed(spans):
            replacement = replacement.replace("\r\n", "\n").replace("\r", "\n")
            if bom and start == 0:
                replacement = replacement.removeprefix(bom)
            raw = raw[: offsets[start]] + replacement.replace("\n", ending) + raw[offsets[end] :]
        new_text = bom + raw
        command = self._write_command(relative, new_text)
        result = await self.context.sandbox.run(
            self.context.workspace, command, expected_files=self.context.expected_files
        )
        return result

    async def _run_shell(self, args: RunShell) -> Outcome:
        return await self.context.sandbox.run(
            self.context.workspace,
            args.command,
            args.timeout,
            readonly=self.context.runtime.state.mode == "plan",
        )

    async def _run_tests(self, args: RunTests) -> Outcome:
        return await run_tests(
            self.context.workspace,
            self.context.sandbox,
            args.command,
            args.timeout,
            runner=SandboxRunner,
        )

    @staticmethod
    def _write_command(path: str, content: str) -> str:
        encoded = base64.b64encode(content.encode()).decode()
        program = (
            "from pathlib import Path; import base64; "
            f"p=Path({path!r}); p.parent.mkdir(parents=True,exist_ok=True); "
            f"p.write_bytes(base64.b64decode({encoded!r}))"
        )
        return "python -c " + shlex.quote(program)


class WorkflowTools:
    def __init__(self, context: ToolContext):
        self.context = context

    async def execute(self, call: ToolCall) -> Outcome:
        model = SCHEMAS.get(call.name, (None,))[0]
        if model is None or call.name not in WORKFLOW_TOOLS:
            return Outcome(status="blocked", reason="unknown_workflow_tool")
        try:
            args = model.model_validate(call.arguments)
        except Exception as exc:
            return Outcome(status="blocked", reason="invalid_arguments", content=str(exc))
        try:
            return await self.context.runtime.workflow_tool(call.name, args.model_dump())
        except Exception as exc:
            return Outcome(status="error", reason=type(exc).__name__, content=str(exc))


class ToolRegistry:
    """Facade that keeps lifecycle governance in Runtime and tool domains explicit."""

    def __init__(self, context: ToolContext):
        self.workspace = WorkspaceTools(context)
        self.workflow = WorkflowTools(context)

    async def execute(self, call: ToolCall) -> Outcome:
        if call.name in WORKFLOW_TOOLS:
            return await self.workflow.execute(call)
        return await self.workspace.execute(call)
