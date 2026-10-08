"""Approval does not bypass hard safety rules. Hooks may narrow, never widen."""

import shlex
from dataclasses import dataclass
from pathlib import PurePosixPath

from ..config import Settings
from ..storage.workspace import CONTROL, sensitive


@dataclass(frozen=True)
class Decision:
    action: str
    reason: str


def local_python_paths(assignment: str) -> list[str] | None:
    """Only explicit relative import roots; never arbitrary environment assignments."""
    if not assignment.startswith("PYTHONPATH="):
        return None
    paths = assignment.removeprefix("PYTHONPATH=").split(":")
    if any(
        not value or PurePosixPath(value).is_absolute()
        or ".." in PurePosixPath(value).parts
        or any(part in CONTROL for part in PurePosixPath(value).parts)
        or sensitive(PurePosixPath(value))
        for value in paths
    ):
        return None
    return paths


def shell_argv(command: str) -> list[str] | None:
    """Parse one command, leaving expansions and shell programs to explicit approval.

    Quotes around Python code are argument boundaries, not shell operators. This
    classifies syntax only: a simple command can still execute arbitrary code.
    """
    if "\x00" in command:
        raise ValueError("NUL in command")
    if "\\\n" in command:
        return None  # Shell line continuations do not share shlex's argv semantics.
    # Classify shell syntax before shlex: heredoc bodies are not argv and may
    # contain literal unmatched quotes. Shell programs still require approval.
    quote, escaped, unescape = "", False, set()
    for index, char in enumerate(command):
        if escaped:
            escaped = False
        elif char == "\\" and quote != "'":
            if quote == '"' and command[index + 1 : index + 2] in {"$", "`"}:
                unescape.add(index)
            escaped = True
        elif quote:
            if char == quote:
                quote = ""
            elif quote == '"' and char in "$`":
                return None
        elif char in "\"'":
            quote = char
        elif char in ";&|<>()\n\r*?{}[]$`~#":
            return None
    argv = shlex.split(
        "".join(char for index, char in enumerate(command) if index not in unescape)
        if unescape
        else command
    )
    if not argv:
        raise ValueError("empty command")
    if len(argv) > 1 and argv[1] in {"python", "python3"} and local_python_paths(argv[0]):
        argv = argv[1:]
    # An embedded command is not protected by rules on the outer executable.
    executable = PurePosixPath(argv[0]).name or argv[0]
    if executable in {
        "sh",
        "bash",
        "dash",
        "zsh",
        "env",
        "eval",
        "exec",
        "source",
        ".",
        "command",
        "builtin",
        "nohup",
        "timeout",
        "nice",
        "busybox",
        "xargs",
        "sudo",
        "doas",
        "time",
        "!",
        "trap",
    }:
        return None
    if executable == "find" and any(arg in {"-exec", "-execdir", "-ok", "-okdir"} for arg in argv):
        return None
    if "=" in argv[0]:  # Shell environment assignments precede the actual executable.
        return None
    return argv


def command_rule(command: str, settings: Settings) -> Decision | None:
    try:
        argv = shell_argv(command)
    except ValueError:
        return Decision("deny", "invalid_shell")
    embedded_python = argv and PurePosixPath(argv[0]).name.startswith("python") and "-c" in argv
    if argv is None or (embedded_python and any(r.decision == "deny" for r in settings.rules)):
        # Prefix rules cannot prove which commands a shell program will execute.
        # Keep configured hard denials effective even if a human approves the tool.
        if any(rule.decision == "deny" for rule in settings.rules):
            return Decision("deny", "compound_shell_with_deny_rules")
        return Decision("ask", "compound_shell")
    matches = [
        r
        for r in settings.rules
        if argv[: len(r.prefix)] == r.prefix
        or (
            r.decision == "deny"
            and "/" not in r.prefix[0]
            and [PurePosixPath(argv[0]).name, *argv[1 : len(r.prefix)]] == r.prefix
        )
    ]
    if matches:
        rule = max(matches, key=lambda r: {"allow": 0, "ask": 1, "deny": 2}[r.decision])
        return Decision(rule.decision, rule.reason)
    return None


def permission(name: str, effect: str, arguments: dict, settings: Settings, mode: str) -> Decision:
    if settings.read_only and effect == "write":
        return Decision("deny", "read_only" if settings.read_only else "plan_mode")
    if mode == "plan" and effect == "write":
        return Decision("deny", "plan_mode")
    if name == "plan_exit" and settings.read_only:
        return Decision("deny", "read_only")
    if name in {"write_file", "patch_file"} and settings.write_scope is not None:
        path = arguments["path"]
        if not any(path == p or path.startswith(p.rstrip("/") + "/") for p in settings.write_scope):
            return Decision("deny", "write_scope")
    rule = command_rule(arguments["command"], settings) if effect == "process" else None
    if rule and rule.action == "deny":
        return rule
    needs_approval = effect in {"write", "process"} or name == "plan_exit"
    if rule and rule.action == "ask":
        needs_approval = True
    elif rule and rule.action == "allow":
        return rule
    elif not needs_approval:
        return Decision("allow", "bounded_tool")
    elif settings.approval == "auto":
        return Decision("allow", "authorized_workspace")
    if settings.approval == "never":
        return Decision("deny", "approval_unavailable")
    return Decision("ask", rule.reason if rule else "approval_required")
