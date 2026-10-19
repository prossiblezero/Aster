"""Receipt-based budgets with append-only history between explicit compactions."""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from dataclasses import dataclass

from ..messages import ContextHistoryError as ContextHistoryError
from ..messages import history_groups
from ..providers.codec import request_payload
from .prompt import context_notes, is_context_note, render_prompt, system_prompt, workflow_state
from .view import render_workflow

try:
    import tiktoken
except ImportError:  # pragma: no cover
    tiktoken = None


class ContextBudgetError(ValueError):
    pass


@dataclass(frozen=True)
class ContextReport:
    estimated_tokens: int
    budget: int
    compacted: bool
    removed_messages: int
    sections: dict[str, int]
    policy_version: str = "deterministic-v10"
    archive_hashes: tuple[str, ...] = ()
    estimate_basis: str = "local"
    prefix_drift: bool = False
    first_changed_item: int | None = None
    masked_outputs: int = 0
    context_notes: int = 0


@dataclass(frozen=True)
class ContextCalculation:
    prompt: list[dict]
    report: ContextReport
    history: list[dict]
    messages: list[dict]


class BudgetCalculator:
    def __init__(self):
        self.encoder = tiktoken.get_encoding("cl100k_base") if tiktoken else None
        self._scope = None
        self._reasoning_tokens = {}
        self._provider_overhead = 0
        self._anchor = None

    def count(self, value) -> int:
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        # Special-token-looking repository text is data, not a tokenizer control sequence.
        return (
            len(self.encoder.encode(text, disallowed_special=()))
            if self.encoder
            else len(text.encode())
        )

    def request_tokens(self, payload, *, overhead=True):
        """Count known opaque replay by its reported tokens, preserving the wire bytes."""
        extra = 0
        if "input" in payload and self._reasoning_tokens:
            items = []
            for item in payload["input"]:
                opaque = item.get("encrypted_content") if item.get("type") == "reasoning" else None
                key = hashlib.sha256(opaque.encode()).hexdigest() if isinstance(opaque, str) else None
                if key in self._reasoning_tokens:
                    extra += self._reasoning_tokens[key]
                    item = {**item, "encrypted_content": ""}
                items.append(item)
            payload = {**payload, "input": items}
        return self.count(payload) + extra + (self._provider_overhead if overhead else 0)

    @staticmethod
    def _canon(value):
        return json.dumps(value, sort_keys=True, ensure_ascii=False)

    def _shape(self, payload):
        # Tool discovery can enlarge schemas without changing a single history item.
        return self._canon({key: payload.get(key) for key in ("model", "tools", "instructions", "system")})

    def _first_changed_item(self, prompt):
        if self._anchor is None:
            return None
        for index, item in enumerate(self._anchor["items"]):
            if index >= len(prompt) or self._canon(prompt[index]) != item:
                return index
        return None

    def estimate(self, prompt, payload) -> tuple[int, str]:
        anchor = self._anchor
        if (anchor is not None and self._shape(payload) == anchor["shape"]
                and self._first_changed_item(prompt) is None):
            added = sum(self.count(item) for item in prompt[len(anchor["items"]):]
                        if not (item.get("role") == "assistant"
                                or item.get("type") in {"reasoning", "function_call", "message"}))
            return anchor["tokens"] + added, "receipt"
        return self.request_tokens(payload), "local"

    def observe(self, payload, reply, prompt=None):
        """Use the successful attempt's receipt, never summed retry accounting."""
        self._anchor = None
        diagnostic = reply.diagnostics
        if diagnostic:
            if diagnostic.get("classification") != "success" or diagnostic.get("flags"):
                return
            usage = diagnostic.get("usage", {})
        else:
            usage = reply.usage
        if usage.get("usage_requests") != 1:
            return
        inputs, outputs = usage.get("input_tokens"), usage.get("output_tokens")
        if type(inputs) is not int or inputs <= 0 or type(outputs) is not int or outputs < 0:
            return
        if prompt:
            self._anchor = {
                "items": [self._canon(item) for item in prompt],
                "tokens": inputs + outputs,
                "shape": self._shape(payload),
            }
        # Compatible endpoints can add instructions not present in the local request.
        # Keep the largest observed positive overhead within this run/model scope.
        self._provider_overhead = max(
            self._provider_overhead, inputs - self.request_tokens(payload, overhead=False)
        )
        if "input" not in payload:
            return
        reasoning = usage.get("reasoning_tokens", outputs)
        if type(reasoning) is not int or not 0 <= reasoning <= outputs:
            return
        for item in reply.raw:
            opaque = item.get("encrypted_content") if item.get("type") == "reasoning" else None
            if isinstance(opaque, str) and opaque:
                # Multiple opaque items conservatively each reserve the reply's total.
                key = hashlib.sha256(opaque.encode()).hexdigest()
                self._reasoning_tokens[key] = max(self._reasoning_tokens.get(key, 0), reasoning)

    def calculate(
        self,
        state,
        user: str,
        memory: str = "",
        skills: str = "",
        structured_history: list[dict] | None = None,
        *,
        budget: int,
        output_reserve: int,
        tools: list[dict] | None = None,
        protocol: str = "responses",
        model: str = "",
        skill_bodies: dict[str, str] | None = None,
        memory_enabled: bool = False,
        skills_available: bool = False,
        workers_available: bool = False,
        environment: str = "",
        archive=None,
        validate_archive=None,
        redact_history=None,
        workflow_notes: bool = True,
    ):
        scope = (state.session_id, protocol, model)
        if scope != self._scope:
            self._scope = scope
            self._reasoning_tokens.clear()
            self._provider_overhead = 0
            self._anchor = None
        system = system_prompt(
            memory_enabled, skills_available, environment, archive,
            worker=bool(state.parent_run_id),
            workers_available=workers_available,
        )
        workflow = workflow_state(state)
        workflow_text_limit = max(
            self.count(state.plan),
            *(self.count(item.get(key, "")) for item in workflow["unfinished_todos"]
              for key in ("content", "acceptance", "resolution")),
            0,
        )
        history = structured_history if structured_history is not None else []
        groups = history_groups(deepcopy(history))
        clipped = False
        archive_hashes = set()
        references = {}
        # Archive before shortening, including injected/recovered historical observations.
        # Only the body is externalized; native call IDs and outcome envelopes stay intact.
        for group in groups:
            names = {}
            for item in group:
                if item.get("type") == "function_call":
                    names[item["call_id"]] = item["name"]
                for call in item.get("tool_calls", []):
                    names[call["id"]] = call["function"]["name"]
                if isinstance(item.get("content"), list):
                    names.update(
                        (block["id"], block["name"])
                        for block in item["content"]
                        if block.get("type") == "tool_use"
                    )
            for item in group:
                if item.get("type") != "function_call_output":
                    continue
                value = item.get("output", "")
                record = self._output_record(value)
                tool = names.get(item["call_id"], "")
                if (
                    archive is not None
                    and isinstance(record.get("content"), str)
                    and tool != "observation_read"
                ):
                    ref = record.get("observation")
                    if ref is None:
                        archived = archive(item["call_id"], tool, record["content"])
                        ref = {"id": archived["id"], "characters": archived["characters"]}
                    if validate_archive is not None:
                        validate_archive(ref)
                    archive_hashes.add(ref["id"])
                    references[item["call_id"]] = ref
        # The native transcript already owns model text, tool observations and user feedback.
        # Ordinary messages remain useful for direct step()/RL callers without a transcript.
        represented_users = {
            x.get("content")
            for x in history
            if x.get("role") == "user" and isinstance(x.get("content"), str)
        }
        has_transcript = any(
            x.get("role") == "assistant"
            or x.get("type") in {"reasoning", "function_call", "function_call_output"}
            for x in history
        )
        messages = [
            m
            for m in state.messages
            if not (
                (m.get("role") in {"assistant", "tool"} and m.get("native", has_transcript))
                or (m.get("role") == "user" and m.get("content") in represented_users)
            )
        ]
        ordinary_ids = {id(message) for message in messages}
        for message in messages:
            if archive is not None and message.get("role") == "tool":
                content = message.get("content", "")
                if isinstance(content, str):
                    # Legacy/imported observations have no native tool call. Preserve
                    # them as observations, never invent protocol calls for old text.
                    key = "legacy-" + hashlib.sha256(content.encode()).hexdigest()
                    ref = archive(key, "historical_observation", content)
                    archive_hashes.add(ref["id"])
        active_bodies = {
            name: body for name, body in (skill_bodies or {}).items() if name in state.active_skills
        }
        sections = {
            "memory": memory,
            "skills": skills,
            "active_skill_bodies": "\n".join(
                f"{name}:\n{body}" for name, body in active_bodies.items()
            ),
        }
        limit = budget - output_reserve
        # Evidence references matter only to a model that can cite them (memory_store);
        # elsewhere they are noise on every result. Loading it shows them for all results.
        citing = any((tool.get("function") or tool).get("name") == "memory_store" for tool in tools or [])
        removed = 0
        full_notes = False
        final_notes = []

        def notes():
            # Training can omit the harness's state notes so trajectories read as a plain dialogue.
            current = {"workflow_state": render_workflow(workflow) if workflow_notes else "", **sections}
            if redact_history is not None:
                redacted = redact_history([
                    {"role": "user", "content": value} for value in current.values()
                ])
                current = {name: item["content"] for name, item in zip(current, redacted, strict=True)}
            return context_notes([] if full_notes else history, current)

        def render():
            nonlocal final_notes
            updates = notes()
            rendered = render_prompt(system, user, messages, groups, updates, redact_history,
                                     references=citing)
            # Persist exactly the redacted note items that were sent to the model.
            final_notes = rendered[-len(updates):] if updates else []
            return rendered

        def payload_of(prompt):
            return request_payload(
                prompt, tools or [], protocol=protocol, model=model,
                output_tokens=output_reserve, prompt_cache_key=state.session_id,
            )

        prompt = render()
        payload = payload_of(prompt)
        full_before = self.request_tokens(payload)
        before, estimate_basis = self.estimate(prompt, payload)
        first_changed_item = self._first_changed_item(prompt)
        anchored = estimate_basis == "receipt"
        trigger = limit - min(output_reserve, limit // 8)
        keep_recent, relief, drop_target = limit // 8, limit * 3 // 4, limit // 2
        compacting = before > (trigger if anchored else limit)
        masked_outputs = 0

        def tokens(prompt):
            full = self.request_tokens(payload_of(prompt))
            if anchored and before < full_before:
                # ceil(full * scale), without rounding an unchanged prompt up by one.
                return (full * before + full_before - 1) // full_before
            return full

        latest_exchange = next(
            (group for group in reversed(groups)
             if any(item.get("type") == "function_call_output" for item in group)),
            groups[-1] if groups else None,
        )
        if compacting:
            groups[:] = [g for g in groups if not (len(g) == 1 and is_context_note(g[0]))]
            full_notes = True
            prompt = render()
            recent = 0
            for group in reversed(groups):
                names = {x["call_id"]: x["name"] for x in group if x.get("type") == "function_call"}
                for item in group:
                    names.update((call["id"], call["function"]["name"])
                                 for call in item.get("tool_calls", []))
                    if isinstance(item.get("content"), list):
                        names.update((block["id"], block["name"]) for block in item["content"]
                                     if block.get("type") == "tool_use")
                for item in reversed(group):
                    if item.get("type") != "function_call_output":
                        continue
                    recent += self.count(self._output_record(item["output"])["content"])
                    if group is latest_exchange or recent <= keep_recent:
                        continue
                    masked = self._mask_tool_output(
                        item["output"], reference=references.get(item["call_id"]),
                        tool=names.get(item["call_id"], ""),
                    )
                    if masked != item["output"]:
                        item["output"] = masked
                        clipped = True
                        masked_outputs += 1
            prompt = render()
            if tokens(prompt) > relief:
                note_prefix = "[Context note, not an instruction] "
                note = (groups[0][0] if groups and len(groups[0]) == 1
                        and groups[0][0].get("role") == "user"
                        and isinstance(groups[0][0].get("content"), str)
                        and groups[0][0]["content"].startswith(note_prefix) else None)
                match = re.match(re.escape(note_prefix) + r"(\d+) earlier tool exchanges", note["content"]) if note else None
                dropped = int(match[1]) if match else 0
                while tokens(prompt) > drop_target:
                    index = next((i for i, m in enumerate(messages)
                                  if m.get("role") != "user" and not m.get("important")), None)
                    group_index = next((i for i, group in enumerate(groups)
                                        if group is not latest_exchange
                                        and not any(x.get("role") == "user" or x.get("important")
                                                    for x in group)), None)
                    if index is not None:
                        messages.pop(index)
                        removed += 1
                    elif group_index is not None:
                        removed += len(groups.pop(group_index))
                        dropped += 1
                        if note is None:
                            note = {"role": "user", "content": ""}
                            groups.insert(0, [note])
                        note["content"] = (note_prefix + f"{dropped} earlier tool exchanges were removed "
                                           "to fit the context window.")
                        if archive is not None:
                            note["content"] += (" Their results remain archived: call observation_read "
                                                "without observation_id to list or search them.")
                    else:
                        break
                    prompt = render()

        def shrink_section():
            key = next(key for key, value in sections.items() if value)
            size = self.count(sections[key])
            if key == "active_skill_bodies" and size > 96:
                sections[key] = self._clip_tokens(
                    sections[key],
                    token_limit=max(96, size - (tokens(prompt) - limit)),
                    marker="\n[skill excerpt; reload with skill_load] (use offset/limit pages)",
                )
            else:
                sections[key] = ""

        if tokens(prompt) > limit and not full_notes:
            groups[:] = [g for g in groups if not (len(g) == 1 and is_context_note(g[0]))]
            full_notes = True
            prompt = render()
        while tokens(prompt) > limit:
            index = next(
                (
                    i
                    for i, m in enumerate(messages)
                    if m.get("role") != "user" and not m.get("important")
                ),
                None,
            )
            latest_exchange = next(
                (
                    group
                    for group in reversed(groups)
                    if any(item.get("type") == "function_call_output" for item in group)
                ),
                groups[-1] if groups else None,
            )
            group_index = next(
                (
                    i
                    for i, g in enumerate(groups)
                    if g is not latest_exchange and not any(x.get("role") == "user" for x in g)
                ),
                None,
            )
            # Prefer the latest complete exchange. Older observations have already
            # been archived; reduce their bodies before removing their complete group.
            candidate_groups = [groups[group_index]] if group_index is not None else groups
            candidates = [
                item
                for group in candidate_groups
                for item in group
                if item.get("type") == "function_call_output"
                and self.count(self._output_record(item.get("output", "")).get("content", "")) > 96
            ]
            if index is not None:
                messages.pop(index)
                removed += 1
            elif candidates:
                item = max(
                    candidates,
                    key=lambda x: self.count(self._output_record(x["output"])["content"]),
                )
                body_size = self.count(self._output_record(item["output"])["content"])
                deficit = (tokens(prompt) - limit + len(candidates) - 1) // len(candidates)
                shortened = self._clip_tool_output(
                    item["output"],
                    token_limit=max(96, body_size - deficit),
                    reference=references.get(item["call_id"]),
                )
                if shortened != item["output"]:
                    item["output"] = shortened
                    clipped = True
                elif group_index is not None:
                    removed += len(groups.pop(group_index))
                elif any(sections.values()):
                    shrink_section()
                else:
                    raise ContextBudgetError(
                        "context_budget_exceeded: mandatory request contains an unshrinkable observation"
                    )
            elif group_index is not None:
                removed += len(groups.pop(group_index))
            elif any(sections.values()):
                # Reconstructible recall/skill text yields to the current request and recent evidence.
                shrink_section()
            elif workflow_text_limit > 64:
                # Keep every pending item identity; only shorten reconstructible prose.
                workflow_text_limit = max(64, workflow_text_limit // 2)
                marker = "\n[excerpt; call todo_list with offset/limit for remaining text]"
                workflow["plan"] = self._clip_tokens(
                    state.plan, token_limit=workflow_text_limit, marker=marker
                )
                for item in workflow["unfinished_todos"]:
                    for key in ("content", "acceptance", "resolution"):
                        if key in item:
                            item[key] = self._clip_tokens(
                                item[key], token_limit=workflow_text_limit, marker=marker
                            )
            else:
                raise ContextBudgetError(
                    f"context_budget_exceeded: mandatory request needs {tokens(prompt)} tokens; input budget is {limit}"
                )
            prompt = render()
        total = tokens(prompt) if compacting else before
        retained_ids = {id(message) for message in messages}
        retained_messages = [
            m for m in state.messages if id(m) not in ordinary_ids or id(m) in retained_ids
        ]
        report = ContextReport(
            total,
            limit,
            full_notes or clipped or before != total,
            removed,
            {
                "system": self.count(system),
                "task": self.count(user),
                "tools": self.count(tools or []),
                "workflow_state": self.count(workflow),
                **{key: self.count(value) for key, value in sections.items()},
                "context_notes": self.count(final_notes) if final_notes else 0,
                "history": self.count(prompt[2:]),
                "provider_overhead": self._provider_overhead,
            },
            archive_hashes=tuple(sorted(archive_hashes)),
            estimate_basis=estimate_basis,
            prefix_drift=first_changed_item is not None,
            first_changed_item=first_changed_item,
            masked_outputs=masked_outputs,
            context_notes=len(final_notes),
        )
        return ContextCalculation(
            prompt, report, [item for group in groups for item in group] + final_notes, retained_messages,
        )

    @staticmethod
    def _output_record(value):
        if isinstance(value, str):
            try:
                record = json.loads(value)
            except ValueError:
                record = None
            if isinstance(record, dict) and "content" in record:
                return record
        return {"content": value}

    def _mask_tool_output(self, value, *, reference=None, tool=""):
        record = self._output_record(value)
        content = record["content"]
        if (self.count(content) <= 48 or isinstance(content, str) and content.startswith(
            ("[archived:", "[omitted page;", "[omitted image result]", "[omitted:")
        )):
            return value
        if isinstance(content, list):
            record["content"] = "[omitted image result]"
        elif reference is not None:
            record["content"] = (f"[archived: {reference['characters']} chars; "
                                 f"observation_read observation_id={reference['id']}]")
            record["observation"] = reference
        elif tool == "observation_read":
            record["content"] = "[omitted page; call observation_read again to re-read it]"
        else:
            record["content"] = f"[omitted: {len(content)} chars]"
        record.pop("page", None)
        return json.dumps(record, ensure_ascii=False) if len(record) > 1 else record["content"]

    def _clip_tool_output(self, value, *, token_limit: int, reference=None):
        """Shorten only permitted body text, retaining status, provenance and call pairing."""
        record = self._output_record(value)
        marker = (
            "\n[excerpt; observation_read for archived content]"
            if "observation" in record or reference is not None
            else "\n[tool output clipped]"
        )
        page_kind = record.get("page")
        if page_kind in {"observation", "text"}:
            if page_kind == "text":
                header, body = record["content"].split("\n", 1)
                page = {**json.loads(header), "content": body}
            else:
                page = json.loads(record["content"])

            def page_text():
                if page_kind == "text":
                    return (
                        json.dumps({k: v for k, v in page.items() if k != "content"})
                        + "\n"
                        + page["content"]
                    )
                return json.dumps(page, ensure_ascii=False)

            # Preserve the cursor for exactly the visible characters/records, even
            # when the latest parallel batch must share a smaller global budget.
            while self.count(page_text()) > token_limit:
                if "observations" in page and len(page["observations"]) > 1:
                    page["observations"].pop()
                    page["next_offset"] = page["offset"] + len(page["observations"])
                elif len(page.get("content", "")) > 1:
                    page["content"] = page["content"][: len(page["content"]) // 2]
                    page["next_offset"] = page["offset"] + len(page["content"])
                else:
                    break  # An irreducible page header must fit or explicitly stop.
            content = page_text()
        else:
            content = self._clip_tokens(record["content"], token_limit=token_limit, marker=marker)
        if content == record["content"]:
            return value
        record["content"] = content
        if reference is not None:
            record["observation"] = reference
        return json.dumps(record, ensure_ascii=False) if len(record) > 1 else content

    def _clip_tokens(self, value, *, token_limit: int, marker="\n[tool output clipped]"):
        if isinstance(value, list):
            return [
                {
                    **block,
                    "text": self._clip_tokens(
                        block["text"], token_limit=token_limit, marker=marker
                    ),
                }
                if "text" in block
                else block
                for block in value
            ]
        if not isinstance(value, str) or self.count(value) <= token_limit:
            return value
        count = max(0, token_limit - self.count(marker + "\n") - 4)
        # Keep both the context at the start and the result/error at the end.
        if self.encoder:
            encoded = self.encoder.encode(value, disallowed_special=())
            head = self.encoder.decode(encoded[: (count + 1) // 2])
            tail = self.encoder.decode(encoded[-(count // 2) :]) if count // 2 else ""
        else:
            encoded = value.encode()
            head = encoded[: (count + 1) // 2].decode(errors="ignore")
            tail = encoded[-(count // 2) :].decode(errors="ignore") if count // 2 else ""
        return head + marker + "\n" + tail
