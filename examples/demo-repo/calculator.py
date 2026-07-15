"""A trivial calculator module for the Workproof demo."""


def add(a: int, b: int) -> int:
    """Return the sum of a and b.

    Bug in the base commit: returns a - b instead of a + b.
    The fix (head commit) corrects this to a + b.
    """
    return a + b


def multiply(a: int, b: int) -> int:
    """Return the product of a and b."""
    return a * b
