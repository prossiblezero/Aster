"""Run commands in an isolated workspace copy and reconcile validated changes."""

from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
import signal
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

from ..config import Settings
from ..schema import Outcome
from ..workspace import CONTROL, BoundaryError, Workspace, sensitive

OUTPUT_LIMIT = 4 * 1024 * 1024
DEPENDENCY_DIRECTORIES = {".venv", "node_modules"}


class SandboxRunner:
    def __init__(self, settings: Settings, *, environment: SandboxRunner | None = None):
        self.settings = settings
        self._source = environment
        self._environment: tempfile.TemporaryDirectory | None = None
        self._environment_workspace: Path | None = None
        self._generation = 0
        self._busy = False

    @property
    def environment_generation(self) -> int:
        return (self._source or self)._generation

    def close(self) -> None:
        if self._busy:
            raise RuntimeError("execution_environment_busy")
        self._drop_environment()

    def _drop_environment(self) -> None:
        if self._environment is not None:
            self._environment.cleanup()
            self._environment = None
            self._environment_workspace = None
            self._generation += 1

    @staticmethod
    def _validate_dependencies(root: Path) -> None:
        for name in DEPENDENCY_DIRECTORIES:
            directory = root / name
            if not directory.exists() and not directory.is_symlink():
                continue
            if directory.is_symlink() or not directory.is_dir():
                raise BoundaryError("invalid_dependency_directory")
            for path in directory.rglob("*"):
                if path.is_symlink():
                    try:
                        target = path.resolve(strict=True)
                    except (OSError, RuntimeError) as exc:
                        raise BoundaryError("invalid_dependency_symlink") from exc
                    interpreter = (
                        name == ".venv"
                        and path.parent == directory / "bin"
                        and path.name in {"python", "python3", f"python{sys.version_info.major}.{sys.version_info.minor}"}
                        and target == Path(sys.executable).resolve()
                    )
                    if not target.is_relative_to(directory) and not interpreter:
                        raise BoundaryError("dependency_symlink_escape")
                elif not (path.is_file() or path.is_dir()):
                    raise BoundaryError("dependency_special_file")

    @contextmanager
    def _execution_directory(self, workspace: Workspace, constrained: bool):
        persistent = self.settings.reuse_environment and not constrained and self._source is None
        temporary = None
        borrowed = {}
        lease = {"retain": False}
        if persistent:
            if self._environment_workspace != workspace.root:
                self._drop_environment()
            if self._environment is None:
                self._environment = tempfile.TemporaryDirectory(prefix="aster-env-")
                self._environment_workspace = workspace.root
            root = Path(self._environment.name)
            self._validate_dependencies(root)
            # The source snapshot is always fresh. Only dependencies survive commands.
            for path in root.iterdir():
                if path.name in DEPENDENCY_DIRECTORIES:
                    continue
                if path.is_dir() and not path.is_symlink():
                    shutil.rmtree(path)
                else:
                    path.unlink()
        else:
            temporary = tempfile.TemporaryDirectory(prefix="aster-exec-")
            root = Path(temporary.name)
            source = self._source or self
            if self.settings.reuse_environment and source._environment is not None:
                source_root = Path(source._environment.name)
                self._validate_dependencies(source_root)
                for name in DEPENDENCY_DIRECTORIES:
                    target = source_root / name
                    if target.is_dir():
                        (root / name).symlink_to(target, target_is_directory=True)
                        borrowed[name] = target
        try:
            yield root, borrowed, persistent, lease
        finally:
            if temporary is not None:
                temporary.cleanup()
            elif not lease["retain"]:
                self._drop_environment()

    @staticmethod
    def environment(root: Path, *, reuse: bool = False) -> dict[str, str]:
        env = {
            "PATH": f"{sys.prefix}/bin:/usr/bin:/bin",
            "HOME": str(root),
            "TMPDIR": str(root / ".tmp"),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
        }
        if reuse:
            packages = root / ".venv" / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
            env.update(PIP_TARGET=str(packages), PYTHONPATH=str(packages))
        return env

    async def run(
        self,
        workspace: Workspace,
        command: str,
        timeout: float = 30,
        *,
        readonly: bool = False,
        expected_files: dict[str, str | None] | None = None,
    ) -> Outcome:
        if self.settings.reuse_environment and self.settings.sandbox != "required":
            return Outcome(status="blocked", reason="isolation_required_for_environment")
        owners = [self] if self._source is None else [self, self._source]
        if any(owner._busy for owner in owners):
            raise RuntimeError("execution_environment_busy")
        for owner in owners:
            owner._busy = True
        try:
            result = await self._run(workspace, command, timeout, readonly=readonly, expected_files=expected_files)
            if self.settings.reuse_environment:
                result.data["environment_generation"] = self.environment_generation
            return result
        except BoundaryError as exc:
            (self._source or self)._drop_environment()
            return Outcome(status="blocked", reason=str(exc), data={
                "environment_generation": self.environment_generation
            })
        finally:
            for owner in owners:
                owner._busy = False

    async def _run(
        self, workspace: Workspace, command: str, timeout: float, *,
        readonly: bool, expected_files: dict[str, str | None] | None,
    ) -> Outcome:
        readonly = readonly or self.settings.read_only
        constrained = readonly or self.settings.write_scope is not None
        if conflict := self._check_expected(workspace, expected_files):
            return conflict
        if self.settings.sandbox == "off":
            if constrained:
                return Outcome(status="blocked", reason="isolation_required_for_scope")
            before = workspace.snapshot()
            result = await self._process(["/bin/sh", "-c", command], workspace.root, timeout)
            result.changed_paths = Workspace.changes(before, workspace.snapshot())
            result.partial_success = bool(result.changed_paths) and result.status != "success"
            result.data["isolation"] = "off"
            return result
        with self._execution_directory(workspace, constrained) as (root, borrowed, persistent, lease):
            before = workspace.snapshot()
            for path in workspace.files():
                if self.settings.reuse_environment and path.relative_to(workspace.root).parts[0] in DEPENDENCY_DIRECTORIES:
                    raise BoundaryError("dependency_path_reserved")
                target = root / path.relative_to(workspace.root)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
            if conflict := self._check_expected(Workspace(root), expected_files):
                return conflict
            (root / ".tmp").mkdir(exist_ok=True)
            launcher = Path(__file__).with_name("linux.py")
            if persistent:
                self._generation += 1
            result = await self._process(
                [
                    sys.executable,
                    "-I",
                    str(launcher),
                    str(root),
                    "on" if self.settings.network else "off",
                    command,
                    *(str(path) for path in borrowed.values()),
                ],
                root,
                timeout,
            )
            if result.exit_code == 125 and "ASTER_SANDBOX_UNAVAILABLE" in result.content:
                if self.settings.sandbox == "best_effort" and not constrained:
                    if conflict := self._check_expected(workspace, expected_files):
                        return conflict
                    before = workspace.snapshot()
                    fallback = await self._process(
                        ["/bin/sh", "-c", command], workspace.root, timeout
                    )
                    fallback.data.update(isolation="off", degraded=True)
                    fallback.changed_paths = Workspace.changes(before, workspace.snapshot())
                    fallback.partial_success = (
                        bool(fallback.changed_paths) and fallback.status != "success"
                    )
                    return fallback
                return Outcome(
                    status="blocked",
                    reason="sandbox_unavailable",
                    content=result.content,
                    data={"isolation": "unavailable"},
                )
            result.data["isolation"] = "landlock+seccomp"
            result.data["environment_generation"] = self.environment_generation
            result.data["environment_reused"] = persistent or bool(borrowed)
            temporary = root / ".tmp"
            if temporary.is_symlink() or not temporary.is_dir():
                temporary.unlink(missing_ok=True)
            else:
                shutil.rmtree(temporary)
            try:
                if persistent:
                    self._validate_dependencies(root)
                for name, target in borrowed.items():
                    if not (root / name).is_symlink() or (root / name).resolve() != target:
                        raise BoundaryError("dependency_mount_changed")
                # Validate entries before opening them: FIFO reads have no process timeout.
                violations = [
                    str(p.relative_to(root))
                    for p in root.rglob("*")
                    if not (
                        (persistent and p.relative_to(root).parts[0] in DEPENDENCY_DIRECTORIES)
                        or p.relative_to(root).parts[0] in borrowed
                    )
                    and (p.is_symlink()
                    or not (p.is_file() or p.is_dir())
                    or sensitive(p.relative_to(root))
                    or any(x in CONTROL for x in p.relative_to(root).parts))
                ]
                if violations:
                    result.status = "blocked"
                    result.reason = "sandbox_protected_output"
                    result.data["rejected_paths"] = violations[:20]
                    return result
                after = Workspace(root).snapshot()
                changed = Workspace.changes(before, after)
                if readonly and changed:
                    return Outcome(
                        status="blocked",
                        reason="read_only",
                        content=result.content,
                        data=result.data,
                    )
                result = self._publish(workspace, root, before, after, result)
                lease["retain"] = result.status == "success"
                return result
            except BoundaryError as exc:
                result.status, result.reason = "blocked", str(exc)
                return result
            finally:
                shutil.rmtree(temporary, ignore_errors=True)

    @staticmethod
    def _check_expected(
        workspace: Workspace, expected_files: dict[str, str | None] | None
    ) -> Outcome | None:
        for name, expected in (expected_files or {}).items():
            try:
                path = workspace.path(name)
                current = None
                if path.exists():
                    with path.open("rb") as stream:
                        current = hashlib.file_digest(stream, "sha256").hexdigest()
                if current == expected:
                    continue
            except (OSError, ValueError):
                pass  # Unreadable or replaced with a directory is also stale evidence.
            return Outcome(
                status="blocked",
                reason="fresh_read_required",
                content=f"Read {name} again: it changed while the edit was pending.",
            )
        return None

    def _publish(
        self,
        workspace: Workspace,
        staged: Path,
        before: dict[str, str],
        after: dict[str, str],
        result: Outcome,
    ) -> Outcome:
        changed = Workspace.changes(before, after)
        current = workspace.snapshot()
        if any(current.get(name) != before.get(name) for name in changed):
            result.status, result.reason = "error", "concurrent_workspace_change"
            return result
        removed = before.keys() - after.keys()
        directories = set()
        try:
            # Plan the complete write set before changing anything, including file↔directory
            # replacements. Ignored or newly created user files must never be removed with it.
            for name in changed:
                target = workspace.path(name, write=True)
                if self.settings.write_scope is not None and not any(
                    name == p or name.startswith(p.rstrip("/") + "/")
                    for p in self.settings.write_scope
                ):
                    raise BoundaryError("write_scope")
                if name in after and target.is_dir():
                    for child in [target, *target.rglob("*")]:
                        relative = child.relative_to(workspace.root)
                        if (
                            child.is_symlink()
                            or sensitive(relative)
                            or any(part in CONTROL for part in relative.parts)
                            or (not child.is_dir() and str(relative) not in removed)
                        ):
                            raise BoundaryError("directory_not_empty")
                        if child.is_dir():
                            directories.add(child)
        except BoundaryError as exc:
            result.status, result.reason = "blocked", str(exc)
            return result

        # ponytail: synchronous publication serializes this process's workers. Each file is
        # atomic; the batch is not. On disk errors keep/report applied changes, never roll
        # back over an external editor's work. Separate Aster processes need an OS lock.
        try:
            for name in sorted(removed, key=lambda name: name.count("/"), reverse=True):
                workspace.path(name, write=True).unlink(missing_ok=True)
            for directory in sorted(directories, key=lambda path: len(path.parts), reverse=True):
                directory.rmdir()
            for name in changed:
                if name not in after:
                    continue
                target = workspace.path(name, write=True)
                target.parent.mkdir(parents=True, exist_ok=True)
                fd, pending = tempfile.mkstemp(dir=target.parent)
                os.close(fd)
                try:
                    shutil.copy2(staged / name, pending)
                    os.replace(pending, target)
                finally:
                    Path(pending).unlink(missing_ok=True)
        except (OSError, BoundaryError) as exc:
            result.status, result.reason = "error", "workspace_publish_failed"
            result.data["publish_error"] = type(exc).__name__
        result.changed_paths = Workspace.changes(current, workspace.snapshot())
        result.partial_success = bool(result.changed_paths) and result.status != "success"
        return result

    async def _process(self, argv: list[str], cwd: Path, timeout: float) -> Outcome:
        started = time.monotonic()
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=cwd,
            env=self.environment(cwd, reuse=self.settings.reuse_environment and self.settings.sandbox != "off"),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
        chunks = bytearray()
        output_bytes = 0

        async def drain():
            nonlocal output_bytes
            while chunk := await process.stdout.read(8192):
                output_bytes += len(chunk)
                if len(chunks) < OUTPUT_LIMIT:
                    chunks.extend(chunk[: OUTPUT_LIMIT - len(chunks)])
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
            data={"output_bytes": output_bytes, "captured_bytes": len(chunks),
                  "output_truncated": output_bytes > len(chunks), "output_limit_bytes": OUTPUT_LIMIT},
        )
