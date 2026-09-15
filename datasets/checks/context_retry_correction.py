import pytest
from solution import run_with_retry


def test_fix():
    calls = []

    def succeeds_on_fourth():
        calls.append(1)
        if len(calls) < 4:
            raise TimeoutError("temporary")
        return "recovered"

    assert run_with_retry(succeeds_on_fourth) == "recovered"
    assert len(calls) == 4
    calls.clear()
    failure = TimeoutError("final")

    def always_fails():
        calls.append(1)
        raise failure

    with pytest.raises(TimeoutError) as caught:
        run_with_retry(always_fails)
    assert caught.value is failure
    assert len(calls) == 4


def test_regression():
    assert run_with_retry(lambda: 7) == 7
    calls = []
    failure = OSError("permanent")

    def fails_once():
        calls.append(1)
        raise failure

    with pytest.raises(OSError) as caught:
        run_with_retry(fails_once)
    assert caught.value is failure
    assert len(calls) == 1
