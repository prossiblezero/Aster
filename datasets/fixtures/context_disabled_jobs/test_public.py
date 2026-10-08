from scheduler import plan_jobs


def test_default_plan():
    assert plan_jobs(["Build", "Lint"], {}) == ["Build", "Lint"]
