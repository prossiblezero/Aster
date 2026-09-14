"""Approval does not bypass hard safety rules. Hooks may narrow, never widen."""

import shlex
from dataclasses import dataclass

from ..config import Settings


@dataclass(frozen=True)
class Decision:
    action: str
    reason: str


def command_rule(command: str, settings: Settings) -> Decision | None:
    # A small grammar is deliberate: complicated shell syntax never gets a reusable allow.
    if any(c in command for c in ";|&><$`\n()*?{}"):
        # Prefix rules cannot prove which commands a shell program will execute.
        # Keep configured hard denials effective even if a human approves the tool.
        if any(rule.decision == "deny" for rule in settings.rules):
            return Decision("deny", "compound_shell_with_deny_rules")
        return Decision("ask", "compound_shell")
    try:
        argv = shlex.split(command)
    except ValueError:
        return Decision("deny", "invalid_shell")
    matches = [r for r in settings.rules if argv[: len(r.prefix)] == r.prefix]
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
