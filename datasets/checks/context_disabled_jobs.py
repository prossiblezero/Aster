from scheduler import plan_jobs
from settings import disabled_names


def test_fix():
    raw = {"disabled_jobs": " LINT ,test,, lint , STRASSE "}
    assert disabled_names(raw) == {"lint", "test", "strasse"}
    names = ["Build", "Lint", "Docs", "TEST", "Straße", "Build"]
    assert plan_jobs(names, raw) == ["Build", "Docs", "Build"]
    assert names == ["Build", "Lint", "Docs", "TEST", "Straße", "Build"]


def test_regression():
    assert disabled_names({}) == set()
    assert plan_jobs(["Build", "Lint"], {}) == ["Build", "Lint"]
    assert plan_jobs([], {"disabled_jobs": "lint"}) == []
