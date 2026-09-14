from solution import fib as candidate_impl

# Official prompt helpers remain fixed in the evaluator.


def fib(n: int):
    """Return n-th Fibonacci number.
    >>> fib(10)
    55
    >>> fib(1)
    1
    >>> fib(8)
    21
    """



METADATA = {}


def check(candidate):
    assert candidate(10) == 55
    assert candidate(1) == 1
    assert candidate(8) == 21
    assert candidate(11) == 89
    assert candidate(12) == 144


def test_fix():
    check(candidate_impl)

def test_regression():
    assert callable(candidate_impl)
