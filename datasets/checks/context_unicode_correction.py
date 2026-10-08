from solution import make_key


def test_fix():
    for source, expected in [
        ("  Straße -- 中文  ", "strasse_中文"),
        ("A...B___C", "a_b_c"),
        ("École № ２", "école_２"),
        ("---", ""),
    ]:
        assert make_key(source) == expected


def test_regression():
    assert make_key("ABC123") == "abc123"
    assert make_key("") == ""
    assert make_key("a1") == "a1"
