"""A small, root-scoped filesystem MCP server, runnable independently of the agent."""

from __future__ import annotations

import argparse
import hashlib
import os
import stat
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
from uuid import uuid4

from .storage.workspace import Workspace

MAX_FILE_BYTES = 1024 * 1024


def create_server(root: Path, *, allow_write=False):
    from mcp.server import MCPServer
    from mcp.server.mcpserver.exceptions import ToolError
    from mcp.types import ToolAnnotations

    if not root.is_dir():
        raise ValueError("filesystem MCP root must be an existing directory")
    workspace = Workspace(root)
    server = MCPServer("aster-filesystem", log_level="ERROR")
    read = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
    write = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=False)

    def tool(annotations):
        def register(fn):
            @wraps(fn)
            def checked(*args, **kwargs):
                try:
                    return fn(*args, **kwargs)
                except (OSError, ValueError) as exc:
                    # Expected path/freshness errors must reach the model as corrective feedback.
                    raise ToolError(str(exc)) from None

            return server.tool(annotations=annotations)(checked)
        return register

    @contextmanager
    def parent(raw: str, *, writing=False):
        target = workspace.path(raw, write=writing)
        parts = target.relative_to(workspace.root).parts
        fd = os.open(workspace.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for part in parts[:-1]:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = child
            yield fd, parts[-1] if parts else "."
        finally:
            os.close(fd)

    def file_bytes(fd: int, name: str) -> bytes:
        handle = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        with os.fdopen(handle, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError("only regular files without hard links are allowed")
            data = stream.read(MAX_FILE_BYTES + 1)
            if len(data) > MAX_FILE_BYTES:
                raise ValueError("file exceeds the 1 MiB limit")
            return data

    @tool(read)
    def list_directory(path: str = ".", offset: int = 0, limit: int = 100) -> dict:
        """List one directory in pages. Paths are relative to the configured root."""
        if offset < 0 or not 1 <= limit <= 500:
            raise ValueError("offset must be nonnegative; limit must be 1..500")
        with parent(path) as (fd, name):
            directory = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            try:
                entries = []
                for child in sorted(os.listdir(directory)):
                    relative = str(Path(path) / child)
                    try:
                        workspace.path(relative)
                    except ValueError:
                        continue
                    info = os.stat(child, dir_fd=directory, follow_symlinks=False)
                    if stat.S_ISLNK(info.st_mode) or (stat.S_ISREG(info.st_mode) and info.st_nlink != 1):
                        continue
                    if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
                        continue
                    entries.append({"path": relative, "type": "directory" if stat.S_ISDIR(info.st_mode) else "file"})
            finally:
                os.close(directory)
        end = offset + limit
        return {"entries": entries[offset:end], "total": len(entries),
                "next_offset": end if end < len(entries) else None}

    @tool(read)
    def read_file(path: str, start: int = 1, limit: int = 200) -> dict:
        """Read UTF-8 lines and a whole-file sha256 for subsequent edits; at most 1 MiB."""
        if start < 1 or not 1 <= limit <= 2000:
            raise ValueError("start must be positive; limit must be 1..2000")
        with parent(path) as (fd, name):
            data = file_bytes(fd, name)
        lines = data.decode("utf-8").splitlines(keepends=True)
        end = min(start - 1 + limit, len(lines))
        return {"path": path, "text": "".join(lines[start - 1:end]),
                "start": start, "next_start": end + 1 if end < len(lines) else None,
                "total_lines": len(lines), "sha256": hashlib.sha256(data).hexdigest()}

    if allow_write:
        @tool(write)
        def write_file(path: str, content: str, expected_sha256: str | None = None) -> dict:
            """Create a UTF-8 file, or replace it using the sha256 returned by read_file.

            Parent directories must exist. Omitting expected_sha256 means create-only.
            Concurrent external edits must be coordinated by the caller.
            """
            data = content.encode("utf-8")
            if len(data) > MAX_FILE_BYTES:
                raise ValueError("file exceeds the 1 MiB limit")
            with parent(path, writing=True) as (fd, name):
                mode = 0o600
                if expected_sha256 is not None:
                    current = file_bytes(fd, name)
                    if hashlib.sha256(current).hexdigest() != expected_sha256:
                        raise ValueError("file changed; read_file again before editing")
                    mode = stat.S_IMODE(os.stat(name, dir_fd=fd, follow_symlinks=False).st_mode) & 0o777
                temporary = ".mcp-write-" + uuid4().hex
                handle = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                 mode, dir_fd=fd)
                try:
                    with os.fdopen(handle, "wb") as stream:
                        stream.write(data)
                        stream.flush()
                        os.fsync(stream.fileno())
                    if expected_sha256 is None:
                        # link is atomic create-only, even if another writer creates the target.
                        os.link(temporary, name, src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
                    else:
                        os.replace(temporary, name, src_dir_fd=fd, dst_dir_fd=fd)
                finally:
                    try:
                        os.unlink(temporary, dir_fd=fd)
                    except FileNotFoundError:
                        pass
            return {"path": path, "sha256": hashlib.sha256(data).hexdigest()}

        @tool(write)
        def create_directory(path: str) -> dict:
            """Create one directory under an existing parent. Refuses existing paths."""
            with parent(path, writing=True) as (fd, name):
                os.mkdir(name, mode=0o700, dir_fd=fd)
            return {"path": path}

        @tool(write)
        def delete_file(path: str, expected_sha256: str) -> dict:
            """Delete one previously read file after checking its sha256. Never deletes directories."""
            with parent(path, writing=True) as (fd, name):
                data = file_bytes(fd, name)
                if hashlib.sha256(data).hexdigest() != expected_sha256:
                    raise ValueError("file changed; read_file again before deleting")
                os.unlink(name, dir_fd=fd)
            return {"path": path, "deleted": True}

    return server


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True, help="Existing allowed directory")
    parser.add_argument("--allow-write", action="store_true", help="Expose create, write and delete tools")
    args = parser.parse_args(argv)
    create_server(args.root, allow_write=args.allow_write).run(transport="stdio")


if __name__ == "__main__":
    main()
