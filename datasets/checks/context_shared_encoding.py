import json

from cache import cache_value
from codec import encode
from transport import request_body


def test_fix():
    payload = {"z": ["中文", {"b": 2, "a": 1}], "a": True}
    expected = '{"a":true,"z":["中文",{"a":1,"b":2}]}'.encode()
    for function in [encode, request_body, cache_value]:
        result = function(payload)
        assert result == expected
        assert json.loads(result) == payload


def test_regression():
    for function in [encode, request_body, cache_value]:
        assert function({}) == b"{}"
        assert function([]) == b"[]"
        assert function(None) == b"null"
