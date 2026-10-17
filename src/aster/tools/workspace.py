"""Workspace tools and bounded result paging."""
from __future__ import annotations

import base64
import difflib
import json
import mimetypes
import re
from collections.abc import Iterable
from itertools import islice
from pathlib import Path
from typing import TYPE_CHECKING

from ..safety.runner import SandboxRunner
from ..schema import Outcome, Record, ToolCall
from ..storage.workspace import digest
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

READ_FILE_CHARACTERS = 24_000

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


def _numbered(lines: list[str], low: int, high: int) -> list[str]:
    return [f"{n + 1}: {lines[n]}" for n in range(max(0, low), min(len(lines), high))]


def edit_preview(text: str, regions: list[tuple[int, int]], limit: int = 80) -> str:
    """Edited lines with two lines of context, so the model need not reread its own edit."""
    lines, shown, blocks = text.split("\n"), 0, []
    for start, end in regions:
        first = text.count("\n", 0, start)
        last = text.count("\n", 0, max(start, end - 1))
        low, high = first - 2, last + 3
        if blocks and low <= blocks[-1][1]:
            blocks[-1][1] = max(blocks[-1][1], high)
        else:
            blocks.append([low, high])
    out = []
    for low, high in blocks:
        chunk = _numbered(lines, low, high)
        if shown + len(chunk) > limit:
            out.append(f"[{len(blocks)} edited regions; preview limited to {limit} lines; use read_file for more]")
            break
        out.append("\n".join(chunk))
        shown += len(chunk)
    return "Edited lines (current file):\n" + "\n--\n".join(out)


def closest_lines(text: str, old: str, limit: int = 3) -> str:
    """Show where an unmatched old_text most likely belongs, with current line numbers."""
    lines = text.split("\n")
    wanted = [line.strip() for line in old.split("\n") if line.strip()]
    if not wanted:
        return ""
    stripped = [line.strip() for line in lines]
    anchors = [n for n, line in enumerate(stripped) if line == wanted[0]][:limit]
    if not anchors:
        best = difflib.get_close_matches(wanted[0], stripped, n=limit, cutoff=0.6)
        anchors = sorted({stripped.index(match) for match in best})
    if not anchors:
        return ""
    span = min(len(old.split("\n")), 12)
    return "Closest current lines:\n" + "\n--\n".join(
        "\n".join(_numbered(lines, n - 2, n + span + 2)) for n in anchors
    )


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
        selected, characters, truncated = [], 0, False
        for line in lines[args.start - 1:args.end]:
            size = len(line) + bool(selected)
            if characters + size > READ_FILE_CHARACTERS:
                if not selected:
                    selected.append(line[:READ_FILE_CHARACTERS])
                truncated = True
                break
            selected.append(line)
            characters += size
        end = args.start + len(selected) - 1 if selected else min(args.end, len(lines))
        return Outcome(
            status="success",
            content="\n".join(selected),
            data={
                "path": args.path,
                "start": args.start,
                "end": end,
                "source_sha256": digest(text),
                **({"truncated": True, "next_start": end + 1} if truncated else {}),
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
        try:
            regex = re.compile(args.pattern, re.IGNORECASE) if args.regex else None
        except re.error as exc:
            return Outcome(status="blocked", reason="invalid_regex", content=str(exc))

        def matches():
            for path in self.context.workspace.files(args.path):
                try:
                    if path.stat().st_size > 2 * 1024 * 1024:
                        continue
                    relative = path.relative_to(self.context.workspace.root)
                    lines = path.read_text(encoding="utf-8").splitlines()
                    for index, line in enumerate(lines):
                        if not (regex.search(line[:2000]) if regex is not None
                                else pattern in line.casefold()):
                            continue
                        if not args.context:
                            yield f"{relative}:{index + 1}:{line[:300]}"
                            continue
                        # One record per match keeps offset counting matches; grep -C notation.
                        low, high = max(0, index - args.context), min(len(lines), index + args.context + 1)
                        yield "--\n" + "\n".join(
                            f"{relative}{':' if n == index else '-'}{n + 1}{':' if n == index else '-'}{lines[n][:300]}"
                            for n in range(low, high)
                        )
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
        if result.status == "success":
            result.data["written_sha256"] = digest(args.content.encode())
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
                if start < 0:
                    hint = closest_lines(text, old)
                else:
                    found, position = [], start
                    while position >= 0 and len(found) < 10:
                        found.append(text.count("\n", 0, position) + 1)
                        position = text.find(old, position + 1)
                    hint = ("Occurrences start at lines " + ", ".join(map(str, found))
                            + "; include more surrounding text to make old_text unique.")
                return Outcome(
                    status="blocked",
                    reason="patch_nonunique",
                    content=f"edits[{index}] found {max(2, text.count(old)) if start >= 0 else 0} occurrences; must match exactly once in the original file"
                    + ("\n" + hint if hint else ""),
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
        if result.status == "success":
            result.data.update(path=args.path, written_sha256=digest(new_text.encode()))
            pieces, regions, cursor, size = [], [], 0, 0
            for start, end, replacement in spans:
                replacement = replacement.replace("\r\n", "\n").replace("\r", "\n")
                pieces.append(text[cursor:start])
                size += start - cursor
                regions.append((size, size + len(replacement)))
                pieces.append(replacement)
                size += len(replacement)
                cursor = end
            pieces.append(text[cursor:])
            preview = edit_preview("".join(pieces), regions)
            result.content = (result.content + "\n" if result.content else "") + preview
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
            code=args.code,
            baseline=self.context.baseline_text,
            known=self.context.baseline_signatures,
        )

    @staticmethod
    def _write_command(path: str, content: str) -> str:
        encoded = base64.b64encode(content.encode()).decode()
        program = (
            "from pathlib import Path; import base64; "
            f"p=Path({path!r}); p.parent.mkdir(parents=True,exist_ok=True); "
            f"p.write_bytes(base64.b64decode({encoded!r}))"
        )
        # Keep file contents out of execve arguments, including Python's -c argument.
        return "python - <<'ASTER_WRITE'\n" + program + "\nASTER_WRITE\n"
