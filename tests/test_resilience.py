"""MCP service circuit recovery and cancellation."""

import asyncio

import pytest

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
