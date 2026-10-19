"""Provider usage coverage and one-shot generation budgets remain observable."""

from types import SimpleNamespace

import httpx
import pytest

from aster.config import MODEL_CONTEXT_LIMITS, Settings
from aster.core.runtime import Runtime
from aster.providers.usage import normalize_usage
from aster.safety.redaction import Redactor
from aster.schema import ModelReply, TaskSpec, ToolCall


@pytest.mark.parametrize(
    "protocol, raw",
    [
        (
            "responses",
            {
                "input_tokens": 100,
                "output_tokens": 30,
                "input_tokens_details": {"cached_tokens": 60},
                "output_tokens_details": {"reasoning_tokens": 20},
            },
        ),
        (
            "chat",
            {
                "prompt_tokens": 100,
                "completion_tokens": 30,
                "prompt_tokens_details": {"cached_tokens": 60},
                "completion_tokens_details": {"reasoning_tokens": 20},
            },
        ),
    ],
)
def test_openai_cache_and_reasoning_are_subsets_not_extra_tokens(protocol, raw):
    usage = normalize_usage(raw, protocol)
    assert usage["input_tokens"] == 100 and usage["total_tokens"] == 130
    assert usage["cache_read_input_tokens"] == 60 and usage["reasoning_tokens"] == 20
    assert usage["cache_read_input_tokens_requests"] == usage["reasoning_tokens_requests"] == 1
    assert "cache_creation_input_tokens" not in usage


def test_anthropic_cache_counts_are_additional_input_and_missing_is_unknown():
    raw = {
        "input_tokens": 10,
        "output_tokens": 20,
        "cache_read_input_tokens": 100,
        "cache_creation_input_tokens": 50,
    }
    usage = normalize_usage(raw, "anthropic")
    assert usage["uncached_input_tokens"] == 10
    assert usage["input_tokens"] == 160 and usage["total_tokens"] == 180
    assert "reasoning_tokens" not in usage
    del raw["cache_creation_input_tokens"]
    partial = normalize_usage(raw, "anthropic")
    assert partial["uncached_input_tokens"] == 10
    assert partial["cache_read_input_tokens"] == 100
    assert "input_tokens" not in partial and "total_tokens" not in partial


def test_reported_zero_does_not_fall_back_to_another_field_or_become_unknown():
    usage = normalize_usage(
        {
            "input_tokens": 0,
            "prompt_tokens": 99,
            "output_tokens": 0,
            "total_tokens": 0,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens_details": {"reasoning_tokens": True},
        }
    )
    assert usage["input_tokens"] == usage["total_tokens"] == 0
    assert usage["cache_read_input_tokens"] == 0
    assert usage["cache_read_input_tokens_requests"] == 1
    assert "reasoning_tokens" not in usage
    assert "total_tokens" not in normalize_usage({"input_tokens": 2})


async def test_runtime_retains_partial_usage_coverage_across_requests(tmp_path):
    (tmp_path / "note.txt").write_text("hello")

    class Model:
        replies = iter(
            [
                ModelReply(
                    calls=[ToolCall(name="read_file", arguments={"path": "note.txt"})],
                    usage=normalize_usage(
                        {
                            "input_tokens": 100,
                            "output_tokens": 10,
                            "input_tokens_details": {"cached_tokens": 0},
                        }
                    ),
                ),
                ModelReply(text="hello", usage=normalize_usage(None)),
            ]
        )

        async def complete(self, *_):
            return next(self.replies)

    runtime = Runtime(tmp_path, Settings(completion="off", auto_dream=False), model=Model())
    state = await runtime.run(TaskSpec(prompt="Read note.txt."))
    assert state.phase == "completed"
    assert state.usage["usage_requests"] == 2
    assert state.usage["total_tokens"] == 110 and state.usage["total_tokens_requests"] == 1
    assert state.usage["cache_read_input_tokens"] == 0
    assert state.usage["cache_read_input_tokens_requests"] == 1
    assert "reasoning_tokens" not in state.usage


@pytest.mark.parametrize(
    "overrides, output, source",
    [
        ({}, 32768, "model_profile"),
        ({"model": "unknown"}, 2048, "fallback"),
        ({"model": "unknown", "context_tokens": 131072}, 32768, "context_budget"),
        ({"context_tokens": 4096}, 1024, "context_budget"),
        ({"context_tokens": 4096, "output_tokens": 512}, 512, "explicit"),
        ({"context_tokens": 128000, "output_tokens": 32768}, 32768, "explicit"),
    ],
)
def test_effective_output_budget_is_model_aware_and_preserves_explicit_caps(
    overrides, output, source
):
    settings = Settings(**overrides)
    assert settings.effective_output_tokens == output
    assert settings.output_budget_source == source
    assert settings.identity()["output_tokens"] == output
    assert (
        Settings.model_validate_json(settings.model_dump_json()).effective_output_tokens == output
    )


def test_automatic_output_budget_tracks_model_changes_and_model_caps(monkeypatch):
    settings = Settings()
    settings.model = "unknown"
    assert settings.effective_output_tokens == 2048
    settings.model = "gpt-5.6-luna"
    assert settings.effective_output_tokens == 32768
    monkeypatch.setitem(
        MODEL_CONTEXT_LIMITS, "small", {"window": 24000, "input": 20000, "output": 4096}
    )
    settings.model = "small"
    assert settings.effective_output_tokens == 4096


async def test_effective_budget_reaches_wire_and_truncated_tool_never_executes(
    tmp_path, monkeypatch
):
    requests = []

    def handler(request):
        import json

        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "status": "incomplete",
                "incomplete_details": {"reason": "max_output_tokens"},
                "output": [
                    {
                        "type": "function_call",
                        "name": "write_file",
                        "call_id": "truncated-edit",
                        "arguments": '{"path":"bad.txt","content":"partial',
                    }
                ],
                "usage": {"input_tokens": 10, "output_tokens": 32768},
            },
        )

    original = httpx.AsyncClient
    monkeypatch.setattr(
        "aster.providers.client.httpx.AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs),
    )
    runtime = Runtime(tmp_path, Settings(api_key="fake-key", approval="auto", auto_dream=False))
    state = await runtime.run(TaskSpec(prompt="Write bad.txt."))
    from aster.core.runtime import OUTPUT_LIMIT_RECOVERIES

    assert requests[0]["max_output_tokens"] == 32768
    # Every attempt is cut off: the partial tool call never runs, recoveries are bounded.
    assert state.stop_reason == "provider_error" and state.step == 0
    assert "responses:max_output_tokens" in state.feedback
    assert not (tmp_path / "bad.txt").exists()
    assert len(requests) == 1 + OUTPUT_LIMIT_RECOVERIES
    assert state.usage["output_tokens"] == 32768 * len(requests)


def test_cli_overrides_budget_and_one_shot_caps(tmp_path, monkeypatch, capsys):
    from aster import cli

    seen = {}

    class NoNetworkRuntime:
        def __init__(self, _root, settings, **_kwargs):
            from unittest.mock import AsyncMock

            seen["settings"] = settings
            self.settings = settings
            self.state = None
            self.evidence = SimpleNamespace(
                redactor=Redactor([settings.api_key.get_secret_value()]), listeners=[]
            )
            self.maintenance = SimpleNamespace(drain=AsyncMock(), close=AsyncMock())

        async def run(self, task):
            from aster.schema import RunState

            seen["task"] = task
            self.state = RunState(task=task, phase="completed", final="done fake-budget-key")
            return self.state

    monkeypatch.setenv("ASTER_API_KEY", "fake-budget-key")
    monkeypatch.setattr(cli, "Runtime", NoNetworkRuntime)
    assert (
        cli.main(
            [
                "--cwd",
                str(tmp_path),
                "--env-file",
                str(tmp_path / "no.env"),
                "--context-tokens",
                "128000",
                "--output-tokens",
                "32768",
                "--max-steps",
                "80",
                "--max-turns",
                "60",
                "hello",
            ]
        )
        == 0
    )
    assert seen["settings"].effective_context_tokens == 128000
    assert seen["settings"].effective_output_tokens == 32768
    assert seen["task"].max_steps == 80 and seen["task"].max_turns == 60
    captured = capsys.readouterr()
    assert "95,232 input + 32,768 output" in captured.err
    assert "done [REDACTED]" in captured.out
    assert "fake-budget-key" not in captured.out + captured.err
    with pytest.raises(SystemExit, match="2"):
        cli.main(["--rl-smoke", "--max-steps", "80"])
