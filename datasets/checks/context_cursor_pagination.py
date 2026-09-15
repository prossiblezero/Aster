from copy import deepcopy

import pytest
from solution import slice_after


def test_fix():
    items = [{"id": "a"}, {"id": "b"}, {"id": "a"}, {"id": "c"}]
    before = deepcopy(items)
    assert slice_after(items, "a", 2) == [{"id": "b"}, {"id": "a"}]
    assert slice_after(items, "missing", 2) == []
    assert slice_after(items, "c", 2) == []
    assert slice_after(items, "a", 0) == []
    with pytest.raises(ValueError):
        slice_after(items, limit=-1)
    assert items == before


def test_regression():
    assert slice_after([]) == []
    assert slice_after([{"id": 1}, {"id": 2}, {"id": 3}]) == [{"id": 1}, {"id": 2}]
