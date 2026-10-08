from solution import format_amount


def test_fix():
    for value, expected in [
        ("1.005", "1.01"),
        ("-1.005", "-1.01"),
        ("2.675", "2.68"),
        ("-0.004", "0.00"),
        ("99.995", "100.00"),
    ]:
        assert format_amount(value) == expected


def test_regression():
    for value, expected in [("1.25", "1.25"), ("0", "0.00"), ("12", "12.00"), ("-2.3", "-2.30")]:
        assert format_amount(value) == expected
