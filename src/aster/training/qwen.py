"""Parse raw Qwen completions into Aster actions, without trusting the server's tool parser.

Rollouts must train on the exact tokens a policy produced, so the harness reads the decoded
completion itself instead of an OpenAI-compatible server's re-serialized tool_calls. Two
formats appear in Qwen chat templates:

- hermes JSON (Qwen2.5, Qwen3): <tool_call>{"name": ..., "arguments": {...}}</tool_call>
- XML parameters (Qwen3-Coder, Qwen3.5): <tool_call><function=NAME><parameter=KEY>
  VALUE</parameter></function></tool_call>

A malformed call is reported, never guessed: the episode continues with that error as an
observation, which is what a policy needs to learn from.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

THINK = re.compile(r"<think>.*?</think>\s*", re.S)
TOOL_CALL = re.compile(r"<tool_call>(.*?)(?:</tool_call>|$)", re.S)
FUNCTION = re.compile(r"<function=([^>\s]+)>(.*?)(?:</function>|$)", re.S)
PARAMETER = re.compile(r"<parameter=([^>\s]+)>\n?(.*?)\n?</parameter>", re.S)


@dataclass
class ParsedTurn:
    text: str
    calls: list[tuple[str, dict]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    reasoning: str = ""


def _typed(value: str, schema: dict):
    """XML parameters arrive as text; the tool schema says what they mean."""
    kind = schema.get("type")
    if kind == "string" or (kind is None and "enum" not in schema):
        return value
    if kind == "boolean":
        if value.strip().lower() in {"true", "false"}:
            return value.strip().lower() == "true"
        raise ValueError(f"expected a boolean, got {value[:40]!r}")
    if kind == "integer":
        return int(value.strip())
    if kind == "number":
        return float(value.strip())
    return json.loads(value)


def parse_completion(text: str, tools: list[dict] | None = None) -> ParsedTurn:
    """Split a decoded completion into visible text, reasoning, and tool calls."""
    if "</think>" in text and "<think>" not in text:
        # Thinking templates open <think> inside the prompt, so the completion starts inside it.
        text = "<think>" + text
    reasoning = "\n".join(m.strip() for m in re.findall(r"<think>(.*?)</think>", text, re.S))
    body = THINK.sub("", text)
    if "<think>" in body and "</think>" not in body:
        return ParsedTurn(text="", reasoning=body, errors=["reasoning was cut off before any answer"])
    schemas = {}
    for tool in tools or []:
        spec = tool.get("function", tool)
        schemas[spec.get("name")] = spec.get("parameters", {}).get("properties", {})
    turn = ParsedTurn(text=TOOL_CALL.sub("", body).strip(), reasoning=reasoning)
    complete = re.findall(r"<tool_call>(.*?)</tool_call>", body, re.S)
    if "<tool_call>" in (body.rsplit("</tool_call>", 1)[-1] if complete else body):
        turn.errors.append("a tool call is not closed with </tool_call> (the output may have been cut off)")
    for index, raw in enumerate(complete):
        raw = raw.strip()
        if raw.startswith("{"):
            try:
                data = json.loads(raw)
                name, arguments = data["name"], data.get("arguments", {})
                if isinstance(arguments, str):
                    arguments = json.loads(arguments)
                if not isinstance(arguments, dict):
                    raise TypeError("arguments must be an object")
            except (ValueError, KeyError, TypeError) as exc:
                turn.errors.append(f"tool call {index + 1} is not valid JSON with name and arguments: {exc}")
                continue
            turn.calls.append((name, arguments))
            continue
        match = FUNCTION.search(raw)
        if not match:
            turn.errors.append(f"tool call {index + 1} has neither JSON nor <function=...>")
            continue
        name, inner = match.group(1), match.group(2)
        if not raw.rstrip().endswith("</function>"):
            turn.errors.append(f"tool call {index + 1} is not closed with </function>")
            continue
        arguments, properties = {}, schemas.get(name, {})
        try:
            for key, value in PARAMETER.findall(inner):
                arguments[key] = _typed(value, properties.get(key, {}))
        except ValueError as exc:
            turn.errors.append(f"tool call {index + 1} ({name}) has a malformed parameter: {exc}")
            continue
        turn.calls.append((name, arguments))
    return turn
