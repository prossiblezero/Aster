from solution import run_with_retry


def test_immediate_success():
    assert run_with_retry(lambda: "ok") == "ok"
