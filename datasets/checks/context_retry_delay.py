from solution import retry_delay


def test_fix():
    for value, expected in [("7.5", 7.5), ("-3", 0.0), ("900", 30.0), (" 0.25 ", 0.25)]:
        headers = {"Retry-After": value, "Trace": "keep"}
        assert retry_delay(headers) == expected
        assert headers == {"Retry-After": value, "Trace": "keep"}


def test_regression():
    for headers in [
        {},
        {"Retry-After": "tomorrow"},
        {"Retry-After": "nan"},
        {"Retry-After": "inf"},
        {"Retry-After": None},
    ]:
        assert retry_delay(headers) == 2.0
