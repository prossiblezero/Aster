"""Workspace tools and bounded result paging."""
from __future__ import annotations

import asyncio
import base64
import difflib
import json
import mimetypes
import os
import re
import shlex
import tempfile
from collections.abc import Iterable
from itertools import islice
from pathlib import Path
from typing import TYPE_CHECKING

from ..safety.runner import SandboxRunner
from ..schema import Outcome, Record, ToolCall
from ..storage.workspace import Workspace, digest
from .schemas import (
    ImageRead,
    ListFiles,
    PatchEdit,
    PatchFile,
    ReadFile,
    RunShell,
    RunTests,
    Search,
    ServiceName,
    ServiceStart,
    WriteFile,
)
from .testing import project_test_command, run_tests

# Output trimming around a test command changes only what is shown, and hides the exit code.
SHELL_OUTPUT_TRIM = re.compile(r"\s+2>&1|\s*\|\s*(?:tail|head)(?:\s+-n)?\s+-?\d+\s*$")
# run_tests reasons meaning "not a test run": the command then runs as an ordinary shell command.
NOT_TEST_RUNS = {"test_command_not_supported", "inline_test_command_not_supported",
                 "test_script_not_in_workspace", "isolation_required_for_scope"}

if TYPE_CHECKING:
    from .registry import ToolContext

READ_FILE_CHARACTERS = 24_000
SERVICE_STARTUP_SECONDS = 2.0


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


def grouped_matches(page: list[tuple[str, int, list[tuple[int, str]]]]) -> str:
    """Search hits under one heading per file, like ripgrep --heading.

    `12:` marks a matching line and `11-` a context line; overlapping context is printed once
    and `--` marks a gap. Repeating the path on every line, grep -C style, made search results
    the largest observations Aster carried (36% of resent tool output on fresh30b).
    """
    files: dict[str, dict[int, tuple[str, bool]]] = {}
    for path, index, window in page:
        lines = files.setdefault(path, {})
        for number, text in window:
            lines[number] = (text, lines.get(number, ("", False))[1] or number == index)
    gaps = any(len(window) > 1 for _, _, window in page)  # plain matches need no separators
    out = []
    for path, lines in files.items():
        out.append(f"{path}:")
        last = None
        for number in sorted(lines):
            if gaps and last is not None and number > last + 1:
                out.append("--")
            text, match = lines[number]
            out.append(f"{number + 1}{':' if match else '-'} {text}")
            last = number
    return "\n".join(out)


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


def tolerant_span(text: str, old: str) -> tuple[int, int] | None:
    """Locate old_text whose whole lines differ only in surrounding whitespace.

    Mirrors the fallback order of Codex's apply_patch seek_sequence (exact, then trailing,
    then all surrounding whitespace ignored); only a unique match is accepted.
    """
    wanted = old.split("\n")
    trailing_newline = wanted[-1] == ""
    if trailing_newline:
        wanted = wanted[:-1]
    if not wanted or not any(line.strip() for line in wanted):
        return None
    lines = text.split("\n")
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line) + 1)
    for normalize in (str.rstrip, str.strip):
        target = [normalize(line) for line in wanted]
        found = [n for n in range(len(lines) - len(target) + 1)
                 if [normalize(line) for line in lines[n:n + len(target)]] == target]
        if len(found) == 1:
            first, last = found[0], found[0] + len(target) - 1
            end = starts[last] + len(lines[last]) + (1 if trailing_newline and last + 1 < len(lines) else 0)
            return starts[first], end
        if found:
            return None
    return None


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
                        # One record per match keeps offset counting matches.
                        low, high = max(0, index - args.context), min(len(lines), index + args.context + 1)
                        yield str(relative), index, [(n, lines[n][:300]) for n in range(low, high)]
                except UnicodeError:
                    continue

        page = list(islice(matches(), args.offset, args.offset + args.limit + 1))
        truncated = len(page) > args.limit
        page = page[:args.limit]
        return Outcome(status="success", content=grouped_matches(page), data={
            "offset": args.offset, "count": len(page), "truncated": truncated,
            "next_offset": args.offset + len(page) if truncated else None})

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
        spans, notes = [], []
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
            if start < 0 and not anchored and (span := tolerant_span(text, old)):
                notes.append(f"edits[{index}] matched ignoring whitespace differences")
                spans.append((*span, edit.new_text))
                continue
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
            result.content = ("\n".join(notes) + "\n" if notes else "") + (
                result.content + "\n" if result.content else "") + preview
        return result

    async def _run_shell(self, args: RunShell) -> Outcome:
        if (verified := await self._shell_tests(args)) is not None:
            return verified
        return await self.context.sandbox.run(
            self.context.workspace,
            args.command,
            args.timeout,
            readonly=self.context.readonly,
        )

    async def _shell_tests(self, args: RunShell) -> Outcome | None:
        """A test command sent to run_shell runs as run_tests would, so it is not run twice."""
        command = SHELL_OUTPUT_TRIM.sub("", args.command).strip()
        try:
            argv = shlex.split(command)
        except ValueError:
            return None
        while argv and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", argv[0]):
            argv = argv[1:]
        framework = argv[:1] in (["pytest"], ["jest"], ["npx"]) or argv[:3] in (
            ["python", "-m", "pytest"], ["python", "-m", "unittest"], ["python3", "-m", "pytest"]
        ) or argv[:2] in (["node", "--test"], ["go", "test"])
        if not (framework or project_test_command(command)):
            return None
        outcome = await run_tests(
            self.context.workspace, self.context.sandbox, command.replace("python3 -m", "python -m", 1),
            args.timeout, runner=SandboxRunner,
            baseline=self.context.baseline_text, known=self.context.baseline_signatures,
        )
        if outcome.reason in NOT_TEST_RUNS:
            return None
        outcome.data["shell_verification"] = True
        outcome.content = ("[Aster ran this test command as formal verification in a disposable copy; "
                           "files the tests write are not kept.]\n" + outcome.content)
        return outcome

    @staticmethod
    def _tail(path: Path, limit: int = 2000) -> str:
        try:
            data = path.read_bytes()
        except OSError:
            return ""
        return data[-limit:].decode("utf-8", errors="replace")

    async def _service_start(self, args: ServiceStart) -> Outcome:
        sandbox, workspace = self.context.sandbox, self.context.workspace
        current = sandbox.services.get(args.name)
        if current and current["process"].poll() is None:
            return Outcome(status="blocked", reason="service_running",
                           content=f"{args.name} is already running; stop it first or use another name.")
        directory = Path(tempfile.gettempdir()) / f"aster-services-{os.getuid()}"
        directory.mkdir(mode=0o700, exist_ok=True)
        log = directory / f"{self.context.run_id}-{args.name}.log"
        log.write_bytes(b"")
        before = workspace.snapshot()
        process = sandbox.start_service(workspace, args.command, log)
        sandbox.services[args.name] = {"process": process, "command": args.command, "log": log,
                                       "keep": args.keep_running}
        await asyncio.sleep(SERVICE_STARTUP_SECONDS)  # long enough to bind a port or fail fast
        code = process.poll()
        changed = Workspace.changes(before, workspace.snapshot())
        state = f"running (pid {process.pid})" if code is None else f"exited with code {code}"
        return Outcome(status="success" if code is None else "error",
                       reason="" if code is None else "service_exited", changed_paths=changed,
                       content=f"{args.name}: {state}\n{self._tail(log)}",
                       data={"service": args.name, "running": code is None, "keep_running": args.keep_running})

    async def _service_status(self, args: ServiceName) -> Outcome:
        services = self.context.sandbox.services
        names = [args.name] if args.name else sorted(services)
        if args.name and args.name not in services:
            return Outcome(status="blocked", reason="service_unknown", content=", ".join(sorted(services)) or "none")
        lines = []
        for name in names:
            service = services[name]
            code = service["process"].poll()
            lines.append(f"{name}: {'running' if code is None else f'exited with code {code}'}"
                         f"{' (kept after the task)' if service['keep'] else ''} — {service['command']}"
                         + ("\n" + self._tail(service["log"]) if args.name else ""))
        return Outcome(status="success", content="\n".join(lines) or "No services.")

    async def _service_stop(self, args: ServiceName) -> Outcome:
        if args.name not in self.context.sandbox.services:
            return Outcome(status="blocked", reason="service_unknown")
        code = await asyncio.to_thread(self.context.sandbox.stop_service, args.name)
        return Outcome(status="success", content=f"{args.name} stopped (exit code {code}).")

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
