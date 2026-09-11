"""Run commands in an isolated workspace copy and reconcile validated changes."""

from __future__ import annotations

import asyncio
import os
import shutil
import signal
import sys
import tempfile
import time
from pathlib import Path

from ..config import Settings
from ..schema import Outcome
from ..workspace import CONTROL, BoundaryError, Workspace, sensitive


class SandboxRunner:
    def __init__(self, settings: Settings):
        self.settings = settings

    @staticmethod
    def environment(root: Path) -> dict[str, str]:
        return {
            "PATH": f"{sys.prefix}/bin:/usr/bin:/bin",
            "HOME": str(root),
            "TMPDIR": str(root / ".tmp"),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
        }

    async def run(
        self, workspace: Workspace, command: str, timeout: float = 30, *, readonly: bool = False
    ) -> Outcome:
        if self.settings.sandbox == "off":
            result = await self._process(["/bin/sh", "-c", command], workspace.root, timeout)
            result.data["isolation"] = "off"
            return result
        with tempfile.TemporaryDirectory(prefix="aster-exec-") as directory:
            root = Path(directory)
            before = workspace.snapshot()
            for path in workspace.files():
                target = root / path.relative_to(workspace.root)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
            (root / ".tmp").mkdir(exist_ok=True)
            launcher = Path(__file__).with_name("linux.py")
            result = await self._process(
                [
                    sys.executable,
                    "-I",
                    str(launcher),
                    str(root),
                    "on" if self.settings.network else "off",
                    command,
                ],
                root,
                timeout,
            )
            if result.exit_code == 125 and "ASTER_SANDBOX_UNAVAILABLE" in result.content:
                if self.settings.sandbox == "best_effort":
                    fallback = await self._process(
                        ["/bin/sh", "-c", command], workspace.root, timeout
                    )
                    fallback.data.update(isolation="off", degraded=True)
                    return fallback
                return Outcome(
                    status="blocked",
                    reason="sandbox_unavailable",
                    content=result.content,
                    data={"isolation": "unavailable"},
                )
            result.data["isolation"] = "landlock+seccomp"
            temporary = root / ".tmp"
            if temporary.is_symlink():
                temporary.unlink()
            elif temporary.exists():
                shutil.rmtree(temporary)
            try:
                staged = Workspace(root)
                after = staged.snapshot()
                changed = Workspace.changes(before, after)
                violations = [
                    str(p.relative_to(root))
                    for p in root.rglob("*")
                    if p.is_symlink()
                    or sensitive(p.relative_to(root))
                    or any(x in CONTROL for x in p.relative_to(root).parts)
                ]
                if violations:
                    result.status = "blocked"
                    result.reason = "sandbox_protected_output"
                    result.data["rejected_paths"] = violations[:20]
                    return result
                if readonly and changed:
                    return Outcome(
                        status="blocked",
                        reason="read_only",
                        content=result.content,
                        data={"isolation": "landlock+seccomp"},
                    )
                current = workspace.snapshot()
                if any(current.get(p) != before.get(p) for p in changed):
                    return Outcome(
                        status="error",
                        reason="concurrent_workspace_change",
                        content="Staged result discarded: target files changed concurrently.",
                    )
                # Validate the entire write set before publishing any file.
                for name in changed:
                    workspace.path(name, write=True)
                    if self.settings.write_scope is not None and not any(
                        name == p or name.startswith(p.rstrip("/") + "/")
                        for p in self.settings.write_scope
                    ):
                        raise BoundaryError("write_scope")
                for name in changed:
                    target = workspace.path(name, write=True)
                    if name not in after:
                        target.unlink(missing_ok=True)
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        fd, pending = tempfile.mkstemp(dir=target.parent)
                        os.close(fd)
                        try:
                            shutil.copy2(root / name, pending)
                            os.replace(pending, target)
                        finally:
                            Path(pending).unlink(missing_ok=True)
                result.changed_paths = changed
                result.partial_success = bool(changed) and result.status != "success"
                return result
            finally:
                shutil.rmtree(temporary, ignore_errors=True)

    async def _process(self, argv: list[str], cwd: Path, timeout: float) -> Outcome:
        started = time.monotonic()
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=cwd,
            env=self.environment(cwd),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
        chunks = bytearray()

        async def drain():
            while chunk := await process.stdout.read(8192):
                if len(chunks) < 256 * 1024:
                    chunks.extend(chunk[: 256 * 1024 - len(chunks)])
            await process.wait()

        reader = asyncio.create_task(drain())
        status = "success"
        try:
            await asyncio.wait_for(asyncio.shield(reader), timeout)
            if process.returncode:
                status = "error"
        except (TimeoutError, asyncio.CancelledError) as exc:
            status = "timeout" if isinstance(exc, TimeoutError) else "cancelled"
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await reader
            if status == "cancelled":
                raise
        finally:
            # Prevent a detached background process surviving its shell.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        return Outcome(
            status=status,
            content=chunks.decode("utf-8", errors="replace"),
            exit_code=process.returncode,
            duration_ms=(time.monotonic() - started) * 1000,
        )
