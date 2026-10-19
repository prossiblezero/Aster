"""What the model sees of a tool result, rendered from the harness's full record.

The structured history keeps one JSON record per tool output: status, reason, evidence and
archive references, paging cursors and byte counts. Audit, recovery, context budgeting and
training read that record. The model needs much less: what came back, whether it failed,
and the few cursors or references it must quote to act next. Sending the record itself cost
about 111 tokens per tool result on fresh30b (pi: 2), resent on every later request, and
JSON-escaped every line of code and test output.

``render_output`` turns a record into plain text: an optional bracketed status line, the
content verbatim, and an optional bracketed footer. It is pure and deterministic, so the
rendered prefix stays stable for provider prompt caching, and a record that is already plain
text passes through unchanged.
"""

from __future__ import annotations

import json

# Results the model may cite as evidence (memory_store), so they keep a short reference.
CITABLE = {"read_file", "run_shell", "run_tests"}
# Their own result already shows what changed.
SELF_DESCRIBING_CHANGES = {"write_file", "patch_file"}
PASSTHROUGH_KEYS = ("idempotency_replayed", "original_status", "uncertain", "mcp_endpoint", "failover")


def output_record(value) -> dict:
    if isinstance(value, str):
        try:
            record = json.loads(value)
        except ValueError:
            record = None
        if isinstance(record, dict) and "content" in record:
            return record
    return {"content": value}


def render_output(value, tool: str = "", *, references: bool = False):
    """Plain text for the model; the full record stays in the harness."""
    record = output_record(value)
    content = record.get("content", "")
    if len(record) == 1 or not isinstance(content, str):
        return value  # already plain text, or structured media blocks
    head = []
    status, reason = record.get("status"), record.get("reason")
    if status and status != "success":
        head.append(status + (f": {reason}" if reason else ""))
    elif reason:
        head.append(reason)
    changed = record.get("changed_paths") or []
    if changed and tool not in SELF_DESCRIBING_CHANGES:
        head.append("changed: " + ", ".join(changed[:8]) + (f" (+{len(changed) - 8} more)" if len(changed) > 8 else ""))
    head.extend(f"{key}: {record[key]}" for key in PASSTHROUGH_KEYS if key in record)
    if record.get("updated"):
        head.append(f"updated: version {str(record.get('previous_version'))[:12]} -> {str(record.get('version'))[:12]}")
    if record.get("source_ref"):
        head.append(f"source_ref {record['source_ref']}")
    elif references and tool in CITABLE and record.get("evidence_id"):
        head.append(f"evidence {record['evidence_id']}")
    tail = []
    if tool == "read_file" and record.get("truncated") and record.get("next_start"):
        tail.append(f"more from line {record['next_start']}")
    if record.get("truncated") and record.get("next_offset") is not None:
        shown = f"{record['count']} of {record['total']} shown; " if "count" in record and "total" in record else ""
        tail.append(f"{shown}next_offset={record['next_offset']}")
    if record.get("output_truncated"):
        tail.append(f"output truncated: kept {record.get('captured_bytes')} of {record.get('output_bytes')} bytes")
    observation = record.get("observation")
    if observation and not content.startswith("[archived:"):
        tail.append(f"full output: observation_read observation_id={observation['id']}")
    parts = (["[" + "; ".join(head) + "]"] if head else []) + ([content] if content else []) + (
        ["[" + "; ".join(tail) + "]"] if tail else [])
    return "\n".join(parts) if parts else "[done]"


def render_workflow(state: dict) -> str:
    """The workflow-state context note as a few readable lines instead of a JSON object."""
    lines = []
    if state.get("mode") and state["mode"] != "work":
        lines.append(f"mode: {state['mode']}")
    if state.get("plan"):
        lines.append("plan: " + state["plan"])
    for todo in state.get("unfinished_todos") or []:
        lines.append(f"todo {todo.get('id', '')} [{todo.get('status', '')}]: {todo.get('content', '')}"
                     + (f" (acceptance: {todo['acceptance']})" if todo.get("acceptance") else "")
                     + (f" (resolution: {todo['resolution']})" if todo.get("resolution") else ""))
    if state.get("active_skills"):
        versions = state.get("active_skill_versions") or {}
        lines.append("active skills: " + ", ".join(
            f"{name}@{versions[name][:12]}" if name in versions else name for name in state["active_skills"]))
    changed = state.get("changed_paths") or []
    if changed:
        count = state.get("changed_path_count", len(changed))
        lines.append("changed files: " + ", ".join(changed) + (f" (+{count - len(changed)} more)" if count > len(changed) else ""))
    verification = state.get("verification") or {}
    if verification.get("state") and (changed or verification["state"] != "missing"):
        lines.append("verification: " + verification["state"]
                     + (f" ({verification['command']})" if verification.get("command") else ""))
    budget = state.get("budget") or {}
    if budget.get("level") == "critical":
        lines.append(f"budget: critical, {budget.get('remaining_turns')} turns and "
                     f"{budget.get('remaining_steps')} tool steps left"
                     + (f", {budget['remaining_team_tokens']} tokens" if "remaining_team_tokens" in budget else ""))
    elif budget.get("level"):
        lines.append(f"budget: {budget['level']} (at most {round(budget.get('remaining_fraction_at_most', 0) * 100)}% left)")
    for key, value in state.items():
        if key in {"budget", "mode", "plan", "unfinished_todos", "active_skills", "active_skill_versions",
                   "changed_paths", "changed_path_count", "verification"} or value in (None, "", [], {}):
            continue
        if isinstance(value, list):
            lines.append(f"{key.replace('_', ' ')}: " + "; ".join(
                v if isinstance(v, str) else json.dumps(v, ensure_ascii=False) for v in value))
        elif isinstance(value, dict):
            lines.append(f"{key.replace('_', ' ')}: " + "; ".join(
                f"{k} ({v})" if v else k for k, v in value.items()))
        else:
            lines.append(f"{key.replace('_', ' ')}: {value}")
    return "\n".join(lines) or "no files changed yet"
