from cache import cache_value
from codec import encode
from transport import request_body


def test_empty_payload():
    assert encode({}) == request_body({}) == cache_value({}) == b"{}"
