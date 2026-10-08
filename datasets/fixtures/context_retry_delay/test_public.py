from solution import retry_delay


def test_default():
    assert retry_delay({}) == 2.0
