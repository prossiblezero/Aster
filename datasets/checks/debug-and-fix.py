from src.handler import process

def test_fix():
    assert process([1,2,3]) == 6
    assert process([9]) == 9
    assert process([-2,5]) == 3

def test_regression():
    assert process([]) == 0
