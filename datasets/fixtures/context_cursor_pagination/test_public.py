from solution import slice_after


def test_first_page():
    assert slice_after([{"id": "a"}, {"id": "b"}, {"id": "c"}]) == [{"id": "a"}, {"id": "b"}]
