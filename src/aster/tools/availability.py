"""Shared tool availability rules for discovery, loading and execution gates."""

import shlex

from ..config import Settings
from ..safety.policy import permission
from ..schema import RunState
from .testing import TEST_COMMAND_PREFIXES


def unavailable_reason(
    name: str, settings: Settings, state: RunState | None,
    *, approval_available: bool, question_available: bool,
):
    if settings.sandbox == "off" and (
        name == "run_tests"
        or (
            name in {"run_shell", "write_file", "patch_file"}
            and (
                settings.read_only
                or settings.write_scope is not None
                or (state and state.mode == "plan")
            )
        )
    ):
        return "isolation_required_for_scope"
    if name == "service_start":
        if settings.sandbox == "off":
            return "isolation_required_for_scope"
        if settings.read_only or (state and state.mode == "plan"):
            return "read_only" if settings.read_only else "plan_mode"
        if settings.write_scope is not None:
            return "write_scope"  # a service writes the workspace directly; scope is unenforceable
    if name in {"write_file", "patch_file"}:
        if settings.read_only:
            return "read_only"
        if state and state.mode == "plan":
            return "plan_mode"
        if settings.write_scope == []:
            return "write_scope"
    if name == "plan_exit" and settings.read_only:
        return "read_only"
    if name.startswith("memory_") and not settings.memory_enabled:
        return "memory_disabled"
    if name == "worker_finish":
        return "" if state and state.parent_run_id else "worker_only"
    if (name.startswith("worker_") and settings.max_workers == 0
            and (name == "worker_start" or not state or not state.worker_sessions)):
        return "workers_disabled"
    if name == "ask_user" and not question_available:
        return "user_input_unavailable"
    no_approval = settings.approval == "never" or (
        settings.approval == "ask" and not approval_available
    )
    if no_approval and (
        name in {"write_file", "patch_file", "plan_exit"}
        or (name in {"run_shell", "run_tests", "service_start"} and not _has_allowed_command(name, settings, state))
    ):
        return "approval_unavailable"
    return ""


def _has_allowed_command(name: str, settings: Settings, state: RunState | None):
    """An allow rule must apply to this tool and survive higher-priority denials."""
    for rule in settings.rules:
        if rule.decision != "allow":
            continue
        for prefix in TEST_COMMAND_PREFIXES if name == "run_tests" else [rule.prefix]:
            if rule.prefix[: len(prefix)] == prefix:
                candidate = rule.prefix
            elif prefix[: len(rule.prefix)] == rule.prefix:
                candidate = prefix
            else:
                continue
            decision = permission(
                name,
                "process",
                {"command": shlex.join(candidate)},
                settings,
                state.mode if state else "work",
            )
            if decision.action == "allow":
                return True
    return False
