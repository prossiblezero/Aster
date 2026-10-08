from solution import order_jobs


def test_single_job():
    assert order_jobs([{"name": "build", "priority": 1}]) == [{"name": "build", "priority": 1}]
