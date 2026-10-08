from client import request_timeout


def test_default_and_legacy():
    assert request_timeout({}) == 2
    assert request_timeout({"timeout_seconds": 3}) == 3
