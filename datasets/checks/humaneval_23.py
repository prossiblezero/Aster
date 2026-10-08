from solution import strlen as candidate_impl

# Official prompt helpers remain fixed in the evaluator.


def strlen(string: str) -> int:
    """ Return length of given string
    >>> strlen('')
    0
    >>> strlen('abc')
    3
    """



METADATA = {
    'author': 'jt',
    'dataset': 'test'
}


def check(candidate):
    assert candidate('') == 0
    assert candidate('x') == 1
    assert candidate('asdasnakj') == 9

def test_fix():
    check(candidate_impl)

def test_regression():
    assert callable(candidate_impl)
