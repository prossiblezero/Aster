from copy import deepcopy

from solution import redact


def test_fix():
    source = {
        "TOKEN": "secret",
        "events": [{"password": "p", "token_count": 4}, {"nested": {"Api_Key": "k"}}],
    }
    original = deepcopy(source)
    assert redact(source) == {
        "TOKEN": "[redacted]",
        "events": [
            {"password": "[redacted]", "token_count": 4},
            {"nested": {"Api_Key": "[redacted]"}},
        ],
    }
    assert source == original


def test_regression():
    for value in [None, 3, "token", [], {"name": "Ada", "token_count": 2}]:
        assert redact(value) == value
