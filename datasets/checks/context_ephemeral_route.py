from solution import route_for

def test_fix():
    assert route_for("read") == "queue-amber-47"
    assert route_for("write") == "queue-violet-83"

def test_regression():
    assert route_for("unknown") is None
