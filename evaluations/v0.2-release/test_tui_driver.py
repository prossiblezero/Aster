"""Offline checks of external cumulative accounting and cancellation trigger freshness."""

import importlib.util
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "release_tui_driver", Path(__file__).with_name("run_tui.py")
)
DRIVER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DRIVER)


def test_usage_does_not_reset_on_resume_or_count_transport_retries_as_new_turns():
    states = {"old": {"step": 5, "turn": 3}, "continued": {"step": 2, "turn": 1}}
    events = [
        {"run_id": "old", "event": "model_stream", "type": "begin", "attempt": 1},
        {"run_id": "continued", "event": "model_stream", "type": "begin", "attempt": 1},
        {"run_id": "continued", "event": "model_stream", "type": "begin", "attempt": 2},
        {"run_id": "continued", "event": "model_stream", "type": "begin", "attempt": 1},
        {"run_id": "continued", "event": "tool_result", "step": 4},
    ]
    assert DRIVER.budget(states, events) == (9, 5)


def test_cancel_requires_an_observed_generation_without_a_later_terminal():
    begin = {"run_id": "same", "seq": 10, "event": "model_stream", "type": "begin"}
    earlier = {"run_id": "same", "seq": 9, "event": "provider_attempt"}
    other = {"run_id": "other", "seq": 11, "event": "provider_attempt"}
    assert DRIVER.generation_pending(begin, [earlier, begin, other])
    for kind, extra in [
        ("provider_attempt", {}),
        ("model_reply", {}),
        ("model_stream", {"type": "completed"}),
        ("model_stream", {"type": "error"}),
    ]:
        assert not DRIVER.generation_pending(
            begin, [begin, {"run_id": "same", "seq": 11, "event": kind, **extra}]
        )
