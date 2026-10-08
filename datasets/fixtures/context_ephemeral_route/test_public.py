from solution import route_for

def test_unknown():
    assert route_for("unknown") is None
