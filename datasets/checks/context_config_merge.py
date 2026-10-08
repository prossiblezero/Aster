from copy import deepcopy

from solution import merge_config


def test_fix():
    base = {"db": {"host": "local", "secret": "x"}, "tags": ["old"]}
    override = {
        "db": {"secret": None, "port": 5432},
        "tags": ["new"],
        "new": {"absent": None, "ok": 1},
    }
    before = deepcopy((base, override))
    result = merge_config(base, override)
    assert result == {"db": {"host": "local", "port": 5432}, "tags": ["new"], "new": {"ok": 1}}
    assert (base, override) == before
    result["tags"].append("independent")
    assert override["tags"] == ["new"]


def test_regression():
    assert merge_config({"a": 1}, {"a": 2}) == {"a": 2}
    assert merge_config({"keep": [1]}, {}) == {"keep": [1]}
    assert merge_config({}, {}) == {}
