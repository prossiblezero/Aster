"""A process-local circuit with one recovery probe and cancellation-safe leases."""

from __future__ import annotations

import time
from contextlib import contextmanager


class CircuitOpen(RuntimeError):
    def __init__(self, retry_after: float):
        self.retry_after = max(0, retry_after)
        super().__init__(f"service circuit open; retry after {self.retry_after:.1f}s")


class CircuitBreaker:
    def __init__(self, failures=3, cooldown=30.0):
        self.threshold, self.cooldown = failures, cooldown
        self.failures = 0
        self.open_until: float | None = None
        self.probing = False
        self.generation = 0
        self.clock = time.monotonic

    def status(self):
        remaining = max(0, (self.open_until or 0) - self.clock())
        state = "closed" if self.open_until is None else "open" if remaining else "half_open"
        return {"state": state, "failures": self.failures, "retry_after": remaining}

    @contextmanager
    def guard(self, is_failure):
        if self.open_until is not None:
            remaining = self.open_until - self.clock()
            if remaining > 0 or self.probing:
                raise CircuitOpen(remaining)
            self.probing = True
        probe, generation = self.probing, self.generation
        try:
            yield
        except BaseException as exc:
            if generation == self.generation and isinstance(exc, Exception):
                if is_failure(exc):
                    self.failures += 1
                    if probe or self.failures >= self.threshold:
                        self.open_until = self.clock() + self.cooldown
                        self.generation += 1
                else:
                    # An application/auth error still demonstrates a reachable service.
                    self.failures, self.open_until = 0, None
            raise
        else:
            if generation == self.generation:
                self.failures, self.open_until = 0, None
        finally:
            if probe:
                self.probing = False
