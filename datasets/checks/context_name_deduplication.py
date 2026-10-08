from solution import unique_names


def test_fix():
    values = [" Ada ", "ada", "", "  ", "STRASSE", "Straße", "Lin", " LIN "]
    before = values[:]
    assert unique_names(values) == [" Ada ", "STRASSE", "Lin"]
    assert values == before


def test_regression():
    assert unique_names([]) == []
    assert unique_names(["Ada", "Lin"]) == ["Ada", "Lin"]
    assert unique_names(["A", "A"]) == ["A"]
