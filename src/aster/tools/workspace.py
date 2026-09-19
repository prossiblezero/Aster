"""Workspace tools and bounded result paging."""
from __future__ import annotations

import base64
import json
import mimetypes
import shlex
from collections.abc import Iterable
from itertools import islice
from pathlib import Path
from typing import TYPE_CHECKING

from ..safety.runner import SandboxRunner
from ..schema import Outcome, Record, ToolCall
from ..workspace import digest
from .schemas import (
    ImageRead,
    ListFiles,
    PatchEdit,
    PatchFile,
    ReadFile,
    RunShell,
    RunTests,
    Search,
    WriteFile,
)
from .testing import run_tests

if TYPE_CHECKING:
    from .registry import ToolContext

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


def read_edit_source(path: Path, *, normalize: bool = True) -> tuple[str, str]:
    """Derive normalized edit text and a raw-byte version from the same bounded read."""
    limit = 2 * 1024 * 1024
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("file exceeds 2 MiB text limit")
    text = data.decode("utf-8")
    return (text.replace("\r\n", "\n").replace("\r", "\n") if normalize else text), digest(data)


class WorkspaceTools:
    def __init__(self, context: ToolContext):
        self.context = context

    async def execute(self, call: ToolCall) -> Outcome:
        from .registry import validate_arguments
        args = validate_arguments(call, direct=True)
        if isinstance(args, Outcome):
            return args
        return await self.execute_validated(call, args)

    async def execute_validated(self, call: ToolCall, args: Record) -> Outcome:
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
            readonly=self.context.readonly,
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
