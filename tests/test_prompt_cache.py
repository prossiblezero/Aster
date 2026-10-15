"""The wire, saved request and budget share one cache identity across recovery."""
import json

import httpx
import pytest

from aster.config import Settings
from aster.core.runtime import Runtime
from aster.providers.client import ModelClient
from aster.providers.codec import request_payload
from aster.schema import TaskSpec

pytestmark = pytest.mark.core


@pytest.mark.parametrize("stream", [False, True])
async def test_wire_cache_identity_survives_resume_and_separates_runs(tmp_path, monkeypatch, stream):
    sent = []

    def handler(request):
        payload = json.loads(request.content)
        sent.append(payload)
        result = {"status": "completed", "output": [{"type": "message", "role": "assistant",
                  "content": [{"type": "output_text", "text": "Understood."}]}],
                  "usage": {"input_tokens": 100, "output_tokens": 10}}
        if stream:
            wire = "data: " + json.dumps({"type": "response.completed", "response": result}) + "\n\n"
            return httpx.Response(200, content=wire, headers={"content-type": "text/event-stream"})
        return httpx.Response(200, json=result)

    original = httpx.AsyncClient
    monkeypatch.setattr("aster.providers.client.httpx.AsyncClient",
                        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    settings = Settings(auth="none", stream=stream, completion="off", memory_enabled=False)
    runtime = Runtime(tmp_path, settings)
    runtime.start(TaskSpec(prompt="Acknowledge"))
    run_id = runtime.state.run_id
    first = runtime.state.session_id
    prompt, tools, report = runtime.prepare_request()
    payload = request_payload(prompt, tools, model=settings.model,
                              output_tokens=settings.effective_output_tokens, prompt_cache_key=first)
    artifact = next(runtime.evidence.path("runs", run_id, "requests").glob("*.json"))
    assert json.loads(artifact.read_text())["payload"] == payload
    assert report.estimated_tokens == runtime.context.count(payload)
    await runtime._model_turn(prompt, tools)
    assert sent[-1]["prompt_cache_key"] == first
    assert all(sent[-1][key] == value for key, value in payload.items())
    restored = Runtime(tmp_path, settings)
    restored.resume(run_id)
    # A resumed boundary, including a different client process, retains the key.
    prompt, tools, _ = restored.prepare_request()
    await restored.model.complete(prompt, tools, prompt_cache_key=restored.state.session_id)
    assert sent[-1]["prompt_cache_key"] == first
    restored.continue_task("A fresh execution")
    assert restored.state.run_id != run_id
    second = restored.state.session_id
    await restored.run()
    assert sent[-1]["prompt_cache_key"] == second == first
    restored.start(TaskSpec(prompt="Independent task"))
    await restored.run()
    assert sent[-1]["prompt_cache_key"] not in {first, second}


async def test_memory_job_identity_is_separate_from_foreground(tmp_path, monkeypatch):
    sent = []

    def handler(request):
        payload = json.loads(request.content)
        sent.append(payload)
        memory = payload["tools"][0]["name"] == "memory_changes"
        result = {"status": "completed", "output": [{"type": "message", "role": "assistant",
                  "content": [{"type": "output_text", "text": '{"operations": []}' if memory else "Understood."}]}],
                  "usage": {"input_tokens": 100, "output_tokens": 10}}
        return httpx.Response(200, json=result)

    original = httpx.AsyncClient
    monkeypatch.setattr("aster.providers.client.httpx.AsyncClient",
                        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    runtime = Runtime(tmp_path, Settings(auth="none", completion="off"))
    await runtime.run(TaskSpec(prompt="Project invoice amounts use Decimal and ROUND_DOWN; acknowledge the convention."))
    await runtime.maintenance.drain(2)
    foreground = [p for p in sent if not p["prompt_cache_key"].startswith("memory-")]
    background = [p for p in sent if p["prompt_cache_key"].startswith("memory-")]
    assert foreground and background
    assert all(p["prompt_cache_key"] == runtime.state.session_id for p in foreground)
    jobs = list((tmp_path / ".aster/memory/jobs").glob("*.json"))
    # The memory controller owns a persisted job ID, independent of the run ID.
    assert len({p["prompt_cache_key"] for p in background}) == len(background)
    assert {p["prompt_cache_key"] for p in background} == {"memory-" + job.stem for job in jobs}


@pytest.mark.parametrize("protocol", ["chat", "anthropic"])
async def test_other_protocols_do_not_send_responses_cache_field(monkeypatch, protocol):
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={
            "choices": [{"finish_reason": "stop", "message": {"content": "done"}}],
            "stop_reason": "end_turn", "content": [{"type": "text", "text": "done"}],
            "usage": {"input_tokens": 100, "output_tokens": 10},
        })

    original = httpx.AsyncClient
    monkeypatch.setattr("aster.providers.client.httpx.AsyncClient",
                        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    await ModelClient(Settings(protocol=protocol, auth="none")).complete(
        [{"role": "user", "content": "Answer"}], [], prompt_cache_key="test-cache-key"
    )
    assert "prompt_cache_key" not in sent[0]
