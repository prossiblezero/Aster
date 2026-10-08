from solution import format_amount


def test_exact_amount():
    assert format_amount("1.25") == "1.25"
