"""Shared guard for public transaction-boundary quantities.

Every state-changing action that accepts a caller-supplied quantity (buying
seeds or fertilizer, consuming or selling inventory, delivering against a
contract) is a transaction boundary: it commits a real state change, so an
invalid quantity must be rejected before any of that state is touched, not
partway through it. `simulation/processing.py:start_job` already got this
right (`isinstance(x, int) and not isinstance(x, bool) and x > 0`) before
the other transaction sites existed; this factors that same check out once
instead of leaving it to be reimplemented (and drift) at each call site.
"""


def is_positive_int(value) -> bool:
    """True only for a real, non-boolean `int` strictly greater than zero.

    Rejects fractional and non-finite quantities implicitly, not as a
    separate check: only an exact `int` passes `isinstance(value, int)` --
    a `float`, even one holding an integral value like `2.0`, does not, and
    neither does `float('nan')` or `float('inf')`. `bool` is an `int`
    subclass in Python, so it needs its own exclusion or `True` would
    silently mean "quantity 1".
    """
    return isinstance(value, int) and not isinstance(value, bool) and value > 0
