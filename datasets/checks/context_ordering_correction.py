from copy import deepcopy

from solution import order_jobs


def test_fix():
    jobs = [
        {"name": "low", "priority": -1},
        {"name": "z", "priority": 5},
        {"name": "default"},
        {"name": "a", "priority": 5},
        {"name": "zero", "priority": 0},
    ]
    before = deepcopy(jobs)
    assert [job["name"] for job in order_jobs(jobs)] == ["z", "a", "default", "zero", "low"]
    assert jobs == before


def test_regression():
    assert order_jobs([]) == []
    assert order_jobs([{"name": "one", "priority": 1}]) == [{"name": "one", "priority": 1}]
    jobs = [{"name": "b"}, {"name": "a"}]
    assert order_jobs(jobs) == jobs
