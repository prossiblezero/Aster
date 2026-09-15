from solution import redact


def test_plain_value():
    assert redact({"name": "Ada", "count": 2}) == {"name": "Ada", "count": 2}
