"""Tiny isolated child launcher: Landlock filesystem rules and seccomp syscalls.

Executed as a separate interpreter, never via preexec_fn in a threaded parent.
No fallback here: installation failure exits 125 before the command executes.
"""

import ctypes
import errno
import os
import platform
import resource
import socket
import sys
from pathlib import Path


def checked(value: int, operation: str) -> int:
    if value < 0:
        raise OSError(ctypes.get_errno(), operation)
    return value


def restrict(root: Path, network: bool) -> None:
    if sys.platform != "linux" or platform.machine() not in {"x86_64", "aarch64"}:
        raise RuntimeError("Linux x86_64/aarch64 required")
    libc = ctypes.CDLL(None, use_errno=True)
    abi = checked(libc.syscall(444, 0, 0, 1), "landlock ABI")
    if abi < 3:
        raise RuntimeError("Landlock ABI >= 3 required (truncate mediation)")
    handled = (1 << 15) - 1
    ruleset = ctypes.c_uint64(handled)
    fd = checked(
        libc.syscall(444, ctypes.byref(ruleset), ctypes.sizeof(ruleset), 0), "landlock create"
    )

    class PathRule(ctypes.Structure):
        _pack_ = 1
        _fields_ = [("access", ctypes.c_uint64), ("parent_fd", ctypes.c_int)]

    def allow(path: Path, access: int):
        if not path.exists():
            return
        if not path.is_dir():
            access &= 1 | 2 | 4 | (1 << 14)
        parent = os.open(path, os.O_PATH | os.O_CLOEXEC)
        try:
            rule = PathRule(access, parent)
            checked(libc.syscall(445, fd, 1, ctypes.byref(rule), 0), "landlock add rule")
        finally:
            os.close(parent)

    try:
        for raw in (
            "/usr",
            "/bin",
            "/lib",
            "/lib64",
            sys.prefix,
            "/etc/ld.so.cache",
            "/etc/localtime",
            "/etc/ssl",
            "/dev/urandom",
            "/dev/random",
        ):
            allow(Path(raw).resolve(), 1 | 4 | 8)
        allow(Path("/dev/null"), 2 | 4)
        allow(root, handled)
        checked(libc.prctl(38, 1, 0, 0, 0), "no_new_privs")
        checked(libc.syscall(446, fd, 0), "landlock restrict")
    finally:
        os.close(fd)

    sec = ctypes.CDLL("libseccomp.so.2", use_errno=True)
    sec.seccomp_init.argtypes = [ctypes.c_uint32]
    sec.seccomp_init.restype = ctypes.c_void_p
    sec.seccomp_rule_add.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint]
    sec.seccomp_load.argtypes = [ctypes.c_void_p]
    sec.seccomp_release.argtypes = [ctypes.c_void_p]
    sec.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]

    class ArgRule(ctypes.Structure):
        _fields_ = [
            ("arg", ctypes.c_uint),
            ("op", ctypes.c_int),
            ("value", ctypes.c_uint64),
            ("unused", ctypes.c_uint64),
        ]

    sec.seccomp_rule_add_array.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_int,
        ctypes.c_uint,
        ctypes.POINTER(ArgRule),
    ]
    ctx = sec.seccomp_init(0x7FFF0000)  # SCMP_ACT_ALLOW
    if not ctx:
        raise RuntimeError("seccomp initialization failed")
    denied = [
        "ptrace",
        "process_vm_readv",
        "process_vm_writev",
        "mount",
        "umount2",
        "pivot_root",
        "unshare",
        "setns",
        "bpf",
        "perf_event_open",
        "io_uring_setup",
        "open_by_handle_at",
        "setsid",
        "setpgid",
        "kill",
        "tkill",
        "tgkill",
    ]
    if not network:
        denied += ["socket", "connect", "bind", "sendmsg", "sendmmsg"]
    try:
        for name in denied:
            number = sec.seccomp_syscall_resolve_name(name.encode())
            if number >= 0:
                checked(
                    sec.seccomp_rule_add(ctx, 0x00050000 | errno.EPERM, number, 0), "seccomp rule"
                )
        if not network:
            # asyncio wakes its loop through a local pair and send(...), implemented
            # by sendto with a NULL destination. The launcher inherits no socket FDs;
            # socket/connect/bind and sending to an explicit address remain forbidden.
            for name, argument, value in (("socketpair", 0, socket.AF_UNIX), ("sendto", 4, 0)):
                number = checked(sec.seccomp_syscall_resolve_name(name.encode()), f"resolve {name}")
                rule = ArgRule(argument, 1, value, 0)  # SCMP_CMP_NE
                checked(
                    sec.seccomp_rule_add_array(
                        ctx, 0x00050000 | errno.EPERM, number, 1, ctypes.byref(rule)
                    ),
                    f"seccomp {name} rule",
                )
        checked(sec.seccomp_load(ctx), "seccomp load")
    finally:
        sec.seccomp_release(ctx)


def main():
    root, network, command = sys.argv[1:]
    try:
        os.chdir(root)
        resource.setrlimit(resource.RLIMIT_FSIZE, (16 * 1024 * 1024, 16 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))
        resource.setrlimit(resource.RLIMIT_CPU, (120, 120))
        restrict(Path(root), network == "on")
    except Exception as exc:
        print(f"ASTER_SANDBOX_UNAVAILABLE:{type(exc).__name__}:{exc}", file=sys.stderr)
        sys.exit(125)
    os.execvpe("/bin/sh", ["sh", "-c", command], os.environ)


if __name__ == "__main__":
    main()
