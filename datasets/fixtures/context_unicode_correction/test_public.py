from solution import make_key


def test_plain_key():
    assert make_key("ABC123") == "abc123"
