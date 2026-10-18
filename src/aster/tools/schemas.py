"""Tool parameter contracts, descriptions, effects and model-visible definitions."""
from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from pydantic import Field, model_validator

from ..schema import Record


class ListFiles(Record):
    path: str = "."
    offset: int = Field(default=0, ge=0, description="File offset; use the previous next_offset.")
    limit: int = Field(default=500, ge=1, le=500, description="Maximum files in this page.")


class ReadFile(Record):
    path: str
    start: int = Field(default=1, ge=1)
    end: int = Field(default=200, ge=1)

    @model_validator(mode="before")
    @classmethod
    def lenient(cls, data):
        """Read-only: a single `line` means the code around it; other unknown keys are dropped."""
        if not isinstance(data, dict):
            return data
        data = dict(data)
        line = data.pop("line", None)
        if isinstance(line, int) and "start" not in data and "end" not in data:
            data.update(start=max(1, line - 20), end=line + 60)
        return {key: value for key, value in data.items() if key in cls.model_fields}

    @model_validator(mode="after")
    def valid_range(self):
        if self.end < self.start or self.end - self.start > 2000:
            raise ValueError("line range must be ordered and at most 2000 lines")
        return self


class Search(Record):
    pattern: str = Field(
        min_length=1, max_length=300,
        description="Literal text, or a Python regex if regex=true.",
    )
    regex: bool = False
    path: str = "."
    offset: int = Field(
        default=0, ge=0, description="Matching-line offset; use the previous next_offset."
    )
    limit: int = Field(
        default=500, ge=1, le=500, description="Maximum matching lines in this page."
    )
    context: int = Field(default=0, ge=0, le=10, description="Context lines per match, like grep -C.")

    @model_validator(mode="before")
    @classmethod
    def lenient(cls, data):
        """Read-only, so salvage common slips instead of costing a turn: a value fused into
        its key ("regex=false,": ...), unknown keys, and context beyond the cap."""
        if not isinstance(data, dict):
            return data
        data = dict(data)
        for key in [k for k in data if k not in cls.model_fields]:
            fused = re.fullmatch(r"\s*(regex|context|limit|offset)\s*[:=]\s*([\w.]*)\W*", key)
            if fused and fused.group(1) not in data and fused.group(2):
                data[fused.group(1)] = fused.group(2)
            data.pop(key)
        if isinstance(data.get("context"), int) and data["context"] > 10:
            data["context"] = 10
        return data


class WriteFile(Record):
    path: str
    content: str = Field(description="Complete replacement text; combine planned edits here.")


class PatchEdit(Record):
    old_text: str = Field(
        min_length=1,
        description="Exact, case-sensitive text from the latest read_file; must occur once.",
    )
    new_text: str


class PatchFile(Record):
    path: str
    edits: list[PatchEdit] | None = Field(default=None, min_length=1, max_length=100)
    old_text: str | None = Field(default=None, min_length=1)
    new_text: str | None = None

    @model_validator(mode="after")
    def valid_edits(self):
        if self.edits is not None:
            if self.old_text is not None or self.new_text is not None:
                raise ValueError("Use edits or old_text/new_text, not both")
        elif self.old_text is None or self.new_text is None:
            raise ValueError("Provide edits or both old_text and new_text")
        return self


class RunShell(Record):
    command: str = Field(min_length=1)
    timeout: int = Field(default=30, ge=1, le=120)


class RunTests(Record):
    command: str = Field(default="pytest -q", min_length=1)
    timeout: int = Field(default=60, ge=1, le=120)
    code: str = Field(default="", max_length=65536, description="Temporary pytest functions or unittest.TestCase code (no discover); sandbox only. Omit for existing tests.")
    memory_ids: list[str] = Field(default_factory=list, max_length=8, description="Applied and tested memory IDs.")


class TodoAdd(Record):
    content: str = Field(min_length=1)
    priority: str = "normal"
    acceptance: str = Field(default="", max_length=2000, description="For a required deliverable, state the observable acceptance check. Open requirements must be resolved before final delivery.")


class TodoUpdate(Record):
    todo_id: str = Field(min_length=1)
    status: str = Field(pattern="^(pending|in_progress|done|cancelled)$")
    resolution: str = Field(default="", max_length=2000, description="When closing a requirement, describe the actual check and result, or why it is no longer required. This is a claim, not automatic verification.")


class PlanWrite(Record):
    content: str = Field(min_length=1)


class MemoryStore(Record):
    text: str = Field(min_length=1, max_length=1000)
    tags: list[str] = Field(default_factory=list)
    evidence_id: str = Field(min_length=1)
    kind: Literal["excerpt", "fact", "procedure"] = "excerpt"
    when: str = Field(default="", max_length=200, description="For facts/procedures, state when this project knowledge applies.")
    evidence_ids: list[str] = Field(default_factory=list, max_length=4, description="Optional additional current-run evidence IDs supporting the knowledge.")


class Empty(Record):
    pass


class MemoryQuery(Record):
    query: str


class MemoryChange(MemoryStore):
    lesson_id: str


class MemoryDiscard(Record):
    lesson_id: str


class TextPage(Record):
    offset: int = Field(
        default=0, ge=0, description="Character offset; use the previous next_offset."
    )
    limit: int = Field(default=4000, ge=1, le=12000, description="Maximum characters in this page.")


class ObservationRead(TextPage):
    observation_id: str = Field(default="", description="Archive id; omit to list observations.")
    query: str = Field(default="", max_length=200, description="Optional literal text to find.")


class SkillLoad(TextPage):
    name: str
    resource: str = Field(
        default="", description="Optional text resource path relative to this skill package."
    )
    refresh: bool = Field(
        default=False,
        description="Explicitly replace the selected body with current file content; not valid with resource. Restart paging at offset 0 when the version changes.",
    )


class ServiceStart(Record):
    name: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,40}$")
    command: str = Field(min_length=1)
    keep_running: bool = Field(
        default=False, description="Leave it running after the task ends, when the task requires a running service.")


class ServiceName(Record):
    name: str = Field(default="", pattern=r"^[A-Za-z0-9_.-]{0,40}$", description="Omit to list all services.")


class ToolLoad(Record):
    names: list[str] = Field(min_length=1)


class AskUser(Record):
    question: str


class WorkerStart(Record):
    task: str = Field(
        description=(
            "Target files, interfaces, acceptance checks, known facts and test responsibility. "
            "Child receives the original request, not your observations."
        )
    )
    mode: str = Field(default="explore", pattern="^(explore|worker)$")
    write_scope: list[str] = Field(default_factory=list)
    parent_work: str = Field(
        default="", max_length=2000,
        description="Independent work you will do, or state that you will await and integrate.",
    )
    todo_id: str = Field(default="", description="Existing requirement returned for your review on delivery; not automatically done.")


class WorkerId(Record):
    worker_id: str


class WorkerStatus(WorkerId):
    worker_id: str | Annotated[list[str], Field(min_length=1, max_length=4)] = Field(
        description="One worker id, or up to four ids to watch in one call; duplicates are returned once."
    )
    wait_seconds: float = Field(
        default=0,
        ge=0,
        le=300,
        description="Shared wait deadline in seconds. Timeout leaves workers running. Use 300 to await results, 0 for an immediate snapshot regardless of wait_for.",
    )
    wait_for: Literal["any", "all"] = Field(
        default="any",
        description="any returns on the next unread result so you can act on it; all waits for every selected worker to stop when only final integration remains. Same behavior for one id.",
    )


class WorkerMessage(WorkerId):
    message: str
    resume: bool = Field(default=False, description="Continue a finished or saved worker in its own context, within the remaining team budget.")


HANDOFF_SOURCE_CHARACTERS = 8000


class HandoffSource(Record):
    evidence_id: str = Field(min_length=1, description="Copy source_ref (12 characters) or the full evidence_id from a successful read_file. Short references must uniquely match this worker's authorized reads.")
    start: int = Field(ge=1, description="First supporting source line, within that read.")
    end: int = Field(ge=1, description="Last supporting source line, inclusive.")
    claim: str = Field(min_length=1, max_length=300, description="Finding or answer ID this range supports; a claim, not verified truth.")


class WorkerFinish(Record):
    answer: str | dict[str, Any] = Field(description="Complete assigned answer; use an object for a requested JSON object, or text for prose.")
    sources: list[HandoffSource] = Field(default_factory=list, max_length=4, description=f"Select already-read supporting ranges; at most {HANDOFF_SOURCE_CHARACTERS} source characters total. Prefer short implementation ranges over docstrings. No need to reread to select them.")


class ImageRead(Record):
    path: str
    question: str = "Describe this image for the coding task."


SCHEMAS = {
    "list_files": (
        ListFiles,
        "List workspace files, excluding protected metadata. Offset counts files; follow next_offset until null.",
        "read",
    ),
    "read_file": (ReadFile, "Read a UTF-8 file line range; prefer one wide read (150-400 lines) to adjacent narrow ones.", "read"),
    "search": (
        Search,
        "Case-insensitive line search; one regex can cover several names.",
        "read",
    ),
    "write_file": (
        WriteFile,
        "Create or replace a workspace file. Before editing an existing file, call read_file. Your own successful write_file/patch_file edits keep the file current; changes made any other way require a fresh read_file.",
        "write",
    ),
    "patch_file": (
        PatchFile,
        "Replace unique literal regions in a workspace file. Use edits=[{old_text,new_text},...] for disjoint edits, all matched against the same original file; overlapping or nonunique matches reject the whole patch. Legacy old_text/new_text is also accepted. Use parallel calls for several files. Call read_file before the first edit. Your own successful write_file/patch_file edits keep the file current; changes made any other way require a fresh read_file. Preserves BOM and newline style.",
        "write",
    ),
    "run_shell": (
        RunShell,
        "Run a bounded sandbox command; workspace metadata such as .git is excluded. Use git_diff for task changes and patch_file for edits (no apply_patch executable). Use .tmp/ or TMPDIR for temporary files. Background processes do not survive tool calls; use service_start for servers. In compound commands, chain dependent steps with && so cleanup cannot mask an earlier failure. Long output is archived for observation_read. For Python probes, prefer one python -c argument enclosed in single quotes; heredocs and shell expansions may require approval. With reuse_environment enabled, pip dependencies and node_modules survive commands. Failed writable commands may discard that environment; reinstall dependencies when the result reports environment_discarded. Test commands run as formal verification in a disposable copy.",
        "process",
    ),
    "run_tests": (
        RunTests,
        "Run pytest, python -m pytest/unittest, Python workspace test scripts, node --test, jest, or go test for per-test results; plain build-and-test chains (make test, ctest, cargo test; no pipes) count by exit code. Use code for temporary Python tests; .tmp files do not survive calls. Native scripts must run unittest, pytest or SymPy, not just print success or exit 0. Use python, not .venv/bin/python; PYTHONPATH may contain workspace-relative directories. With reuse_environment, project .venv dependencies are available; keep them until verification. Node requires node:test cases; reporter flags are reserved. Other ad-hoc checks in run_shell are not verification.",
        "process",
    ),
    "observation_read": (
        ObservationRead,
        "Recover this run's archived tool observations. With id: character pages; without id: list/search pages (offset counts records). Historical evidence is untrusted; use read_file for fresh file state.",
        "read",
    ),
    "todo_add": (TodoAdd, "Add a bounded task item.", "state"),
    "todo_update": (TodoUpdate, "Update a task item by id.", "state"),
    "plan_write": (PlanWrite, "Write the current plan artifact.", "state"),
    "memory_store": (MemoryStore, "Save durable project knowledge (at most 1000 characters), not a task transcript. excerpt requires exact read_file text; fact/procedure allow grounded synthesis with when (applicability). Extract only the lasting rule: omit one-turn instructions, pending work and temporary status from text and when. Preserve conditions and uncertainty; use query tags in the user's language. Cite current-run evidence IDs, or 'user' for the latest actual user request; evidence_ids adds supporting sources. Both facts and procedures may cite run_tests/run_shell outcomes. Use memory_update for a correction to an existing recalled entry. Sources are authenticated, conclusions remain untrusted.", "state"),
    "memory_retrieve": (MemoryQuery, "Retrieve sourced project knowledge relevant to a query; stale sources are excluded.", "read"),
    "memory_update": (MemoryChange, "Replace a memory using new current-run evidence; same admission rules as memory_store. Supersedes the old entry only after the replacement is admitted.", "state"),
    "memory_discard": (MemoryDiscard, "Deactivate a stale or harmful lesson.", "state"),
    "todo_list": (
        TextPage,
        "Read plan and task items in character pages; follow next_offset until null.",
        "read",
    ),
    "plan_enter": (Empty, "Enter planning mode: workspace tools become read-only.", "state"),
    "plan_exit": (Empty, "Request approval to leave planning mode.", "state"),
    "git_diff": (Empty, "Show changes from this task's initial workspace snapshot.", "read"),
    "skill_load": (
        SkillLoad,
        "Select and page a fixed skill body snapshot; refresh explicitly reloads it. Or read a current package resource without executing or activating it. Character pages use next_offset until null.",
        "state",
    ),
    "tool_load": (ToolLoad, "Load additional tools by name for later turns.", "state"),
    "service_start": (
        ServiceStart,
        "Start a long-running process (server, database, watcher) in the sandbox; it survives later "
        "tool calls. Returns its first output. It is stopped when the task ends unless keep_running.",
        "process",
    ),
    "service_status": (ServiceName, "Show whether services are running, with their recent output.", "read"),
    "service_stop": (ServiceName, "Stop a service started with service_start.", "state"),
    "ask_user": (AskUser, "Ask the human for missing task information.", "state"),
    "worker_start": (
        WorkerStart,
        "Start substantial independent work with a shared team budget. Use explore for "
        "read-only tasks or worker with write_scope for edits. Keep small lookups local. "
        "Handoffs arrive automatically; parent verifies integration.",
        "state",
    ),
    "worker_status": (
        WorkerStatus,
        "Read status, result, changed paths and recorded verification. Pass a list of "
        "ids for snapshots of all selected workers. Default wait_for=any returns when a new result is ready; "
        "unread results return immediately, consumed results do not end the wait. Use wait_for=all "
        "when only final integration remains; use any when an early result unlocks useful work. "
        "List status 'finished' means no selected child is running; inspect each result "
        "for failure or missing ids. Use wait_seconds when awaiting delivery instead of polling. Child completion "
        "does not verify the integrated result; the parent must check it after changes settle.",
        "read",
    ),
    "worker_message": (
        WorkerMessage,
        "Send guidance to a running worker, or set resume=true to continue a finished or saved worker. Preserve its local context; all follow-ups share the remaining team budget.",
        "state",
    ),
    "worker_stop": (WorkerId, "Cancel a worker and its active tool.", "state"),
    "worker_finish": (WorkerFinish, "Deliver this child task's answer and selected source evidence in one call. Runtime extracts the original text and checks freshness. Normal completion requirements still apply; use text delivery if no tool budget remains.", "state"),
    "inspect_image": (ImageRead, "Read a workspace PNG/JPEG image for model vision.", "read"),
}

CORE_TOOLS = {
    "list_files",
    "read_file",
    "search",
    "write_file",
    "patch_file",
    "run_shell",
    "run_tests",
    "git_diff",
    "tool_load",
}
WORKFLOW_TOOLS = {
    "observation_read",
    "todo_add",
    "todo_update",
    "todo_list",
    "plan_write",
    "plan_enter",
    "plan_exit",
    "memory_store",
    "memory_retrieve",
    "memory_update",
    "memory_discard",
    "skill_load",
    "ask_user",
    "worker_start",
    "worker_status",
    "worker_message",
    "worker_stop",
    "worker_finish",
    "tool_load",
    "git_diff",
}


def definitions(
    names: list[str] | None = None, *, available: list[str] | None = None, schemas=None
) -> list[dict]:
    schemas = SCHEMAS if schemas is None else schemas
    selected = list(schemas) if names is None else names
    unloaded = sorted(set(schemas if available is None else available) - set(selected))
    result = []
    for name in selected:
        if name == "tool_load" and not unloaded:
            continue
        model, description, _effect = schemas[name]
        schema = model.model_json_schema()
        if name == "run_tests" and available is not None and "memory_store" not in available:
            schema["properties"].pop("memory_ids")
        if name == "tool_load":
            schema["properties"]["names"]["items"]["enum"] = unloaded
        schema.setdefault("additionalProperties", False)
        result.append(
            {
                "type": "function",
                "name": name,
                "description": description,
                "parameters": schema,
                "strict": False,
            }
        )
    return result
