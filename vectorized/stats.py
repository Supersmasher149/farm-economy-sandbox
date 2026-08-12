"""Welford's algorithm, batch/parallel form (component D).

Per-value Welford (`update(x)` one at a time) would mean a Python-level loop
per run, which defeats the point of simulating 100k runs per chunk in numba
and then paying it all back one float at a time in pure Python. Instead each
`update(batch)` call computes the batch's own mean/M2 with plain vectorized
numpy (cheap, O(batch) with no Python-level per-element loop) and folds it
into the running accumulator with Chandan-Golub-LeVeque's parallel-variance
combination formula -- the textbook generalization of Welford's update from
"one more value" to "one more batch of any size." Mean and variance are
accumulated in float64 regardless of the batch dtype (component: "aggregate
in float64"), so >1M accumulated float32 samples don't lose precision to
summation error.
"""

from __future__ import annotations

import numpy as np


class StreamingStats:
    """Online count/mean/variance/min/max for one metric, updated in batches."""

    __slots__ = ("count", "mean", "m2", "minimum", "maximum")

    def __init__(self) -> None:
        self.count = 0
        self.mean = 0.0
        self.m2 = 0.0
        self.minimum = float("inf")
        self.maximum = float("-inf")

    def update(self, batch_values: np.ndarray) -> None:
        """Fold a 1-D batch of samples into the running accumulator."""
        batch = np.asarray(batch_values, dtype=np.float64)
        n_b = batch.size
        if n_b == 0:
            return

        mean_b = float(batch.mean())
        # ddof=0: population variance of the batch, then scaled back to a raw
        # sum-of-squared-deviations (M2) for the merge step below.
        m2_b = float(batch.var(ddof=0)) * n_b

        n_a = self.count
        if n_a == 0:
            self.count, self.mean, self.m2 = n_b, mean_b, m2_b
        else:
            delta = mean_b - self.mean
            total = n_a + n_b
            self.mean = self.mean + delta * (n_b / total)
            self.m2 = self.m2 + m2_b + delta * delta * (n_a * n_b / total)
            self.count = total

        self.minimum = min(self.minimum, float(batch.min()))
        self.maximum = max(self.maximum, float(batch.max()))

    @property
    def variance(self) -> float:
        return self.m2 / self.count if self.count > 1 else 0.0

    @property
    def stddev(self) -> float:
        return self.variance**0.5

    def as_dict(self) -> dict:
        return {
            "count": self.count,
            "mean": self.mean,
            "stddev": self.stddev,
            "min": self.minimum if self.count else None,
            "max": self.maximum if self.count else None,
        }
