"""Service outages, single recovery probes, and real provider retry boundaries."""

import asyncio
import json

import httpx
import pytest

from aster.config import Settings
from aster.providers.client import ModelClient
from aster.providers.errors import ProviderError
from aster.resilience import CircuitBreaker, CircuitOpen

pytestmark = pytest.mark.core


def test_circuit_probe_cancellation_and_late_success():
    circuit = CircuitBreaker(failures=1, cooldown=30)
    now = [100.0]
    circuit.clock = lambda: now[0]
    def outage(exc):
        return isinstance(exc, OSError)
    with circuit.guard(outage):
        with pytest.raises(OSError), circuit.guard(outage):
            raise OSError("offline")
    # The older in-flight success must not close a circuit opened by another request.
    assert circuit.status()["state"] == "open"
    with pytest.raises(CircuitOpen), circuit.guard(outage):
        pytest.fail("an open circuit reached the service")
    now[0] += 31
    with pytest.raises(asyncio.CancelledError), circuit.guard(outage):
        with pytest.raises(CircuitOpen), circuit.guard(outage):
            pytest.fail("more than one recovery probe")
        raise asyncio.CancelledError
    assert not circuit.probing and circuit.status()["state"] == "half_open"
    with circuit.guard(outage):
        pass
    assert circuit.status()["state"] == "closed"


@pytest.mark.parametrize("stream", [False, True])
async def test_provider_circuit_blocks_without_http_and_recovers(monkeypatch, stream):
    calls, healthy = [], [False]

    def handler(request):
        calls.append(request)
        if not healthy[0]:
            return httpx.Response(503, json={"error": {"message": "unavailable"}})
        reply = {"id": "resilience-test", "status": "completed", "output": [
            {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "recovered"}]},
        ]}
        if stream:
            event = {"type": "response.completed", "response": reply}
            return httpx.Response(200, text="data: " + json.dumps(event) + "\n\n",
                                  headers={"content-type": "text/event-stream"})
        return httpx.Response(200, json=reply)

    client_type = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client_type(
        **kwargs, transport=httpx.MockTransport(handler)))
    model = ModelClient(Settings(api_key="synthetic-key", stream=stream), max_attempts=1)
    now = [100.0]
    model.circuit.clock = lambda: now[0]
    for _ in range(3):
        with pytest.raises(ProviderError):
            await model.complete([{"role": "user", "content": "hello"}], [])
    with pytest.raises(ProviderError) as caught:
        await model.complete([{"role": "user", "content": "hello"}], [])
    assert caught.value.details["category"] == "circuit_open"
    assert caught.value.details["attempts"] == 0 and len(calls) == 3
    now[0] += 31
    healthy[0] = True
    assert (await model.complete([{"role": "user", "content": "hello"}], [])).text == "recovered"
    assert len(calls) == 4 and model.circuit.status()["state"] == "closed"
