from client import request_timeout
from settings import load_settings


def test_fix():
    assert load_settings({}) == {"timeout_ms": 2000}
    assert load_settings({"timeout_seconds": 1.5}) == {"timeout_ms": 1500}
    assert load_settings({"timeout_ms": 250, "timeout_seconds": 9}) == {"timeout_ms": 250}
    assert request_timeout({"timeout_ms": 250, "timeout_seconds": 9}) == 0.25
    assert request_timeout({"timeout_ms": 0}) == 0


def test_regression():
    assert request_timeout({}) == 2
    assert request_timeout({"timeout_seconds": 3}) == 3
    assert request_timeout({"timeout_seconds": 0}) == 0
