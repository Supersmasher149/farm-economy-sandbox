"""splitmix64: the only RNG this package uses, in three interchangeable forms.

`simulation/random_events.py` wraps a single `random.Random(seed)` and every
draw serializes through it -- that is precisely what makes a vectorized,
prange-parallel kernel impossible: there is no way to hand 100,000 runs a
`random.Random` each and still call the result "one seed, one deterministic
stream" without serializing them anyway.

splitmix64 replaces that with a scheme where every (run, plot) pair owns an
independent 64-bit state that advances by exactly one mix step per draw. That
makes each run's whole trajectory a pure function of `(master_seed, run_index,
plot_index, draw_number)` -- reorder runs across chunks, change chunk size,
run one run in isolation instead of a batch of 100,000, and every draw is
still bit-identical, because nothing about a run's stream depends on which
other runs share its chunk. That property (chunk-size independence) is what
`scripts/vectorized_validate.py` checks.

This is a *different* determinism model from `simulation/`'s, not a faster
implementation of the same one -- see vectorized/README.md. It intentionally
does not, and cannot, reproduce `simulation/random_events.py` output for the
same seed.

Three call shapes, same algorithm, kept in lockstep on purpose:
  - `mix_scalar` / `next_scalar`: plain Python ints, used by reference.py's
    sequential per-run loop (the thing vectorized results are checked
    against).
  - `next_array`: numpy uint64 arrays, used by orchestrator.py and by
    kernel.py's non-numba fallback.
  - `next_state_and_uniform` (the numba path): same arithmetic again, written
    so `@njit` can compile it, used inside kernel.simulate_chunk.

If you change the constants or the mix steps, change them in all three or
the validation script stops proving anything.
"""

from __future__ import annotations

import numpy as np

MASK64 = 0xFFFFFFFFFFFFFFFF
GOLDEN_GAMMA = 0x9E3779B97F4A7C15
MIX_MULT_1 = 0xBF58476D1CE4E5B9
MIX_MULT_2 = 0x94D049BB133111EB

# Odd constant used to decorrelate a plot's initial state from its run's,
# and from its sibling plots'. Any odd 64-bit constant works; this one is
# splitmix64's own second multiplier, reused for no reason beyond "already
# known to mix well" -- there's nothing meaningful about the specific value.
PLOT_SALT = 0xD1B54A32D192ED03


def mix_scalar(x: int) -> int:
    """One splitmix64 finalizer step: state -> well-mixed 64-bit int."""
    z = (x + GOLDEN_GAMMA) & MASK64
    z = ((z ^ (z >> 30)) * MIX_MULT_1) & MASK64
    z = ((z ^ (z >> 27)) * MIX_MULT_2) & MASK64
    return z ^ (z >> 31)


def next_scalar(state: int) -> tuple[int, float]:
    """Advance a scalar state by one draw; return (new_state, uniform in [0, 1))."""
    state = (state + GOLDEN_GAMMA) & MASK64
    z = state
    z = ((z ^ (z >> 30)) * MIX_MULT_1) & MASK64
    z = ((z ^ (z >> 27)) * MIX_MULT_2) & MASK64
    z = z ^ (z >> 31)
    # Top 53 bits -> float64 in [0, 1), the standard construction (matches
    # what CPython's random.random() does with its own bit source).
    uniform = (z >> 11) * (1.0 / (1 << 53))
    return state, uniform


def run_seed(master_seed: int, run_index: int) -> int:
    """Deterministic per-run seed: splitmix64(master_seed + run_index).

    This is the run's weather/RNG-run stream's initial state directly (not a
    seed that then needs seeding again) -- one mix step is enough entropy to
    decorrelate adjacent run indices.
    """
    return mix_scalar((master_seed + run_index) & MASK64)


def plot_seed(run_state: int, plot_index: int) -> int:
    """Deterministic per-plot seed, decorrelated from its run and siblings."""
    return mix_scalar((run_state ^ (plot_index * PLOT_SALT)) & MASK64)


def next_array(state: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Vectorized splitmix64 step over a uint64 array of independent streams.

    numpy uint64 arithmetic wraps silently on overflow (no OverflowWarning,
    unlike signed dtypes), which is exactly the 64-bit wraparound the scalar
    version gets from `& MASK64` -- so no masking needed here.
    """
    state = state + np.uint64(GOLDEN_GAMMA)
    z = state
    z = (z ^ (z >> np.uint64(30))) * np.uint64(MIX_MULT_1)
    z = (z ^ (z >> np.uint64(27))) * np.uint64(MIX_MULT_2)
    z = z ^ (z >> np.uint64(31))
    uniform = (z >> np.uint64(11)).astype(np.float64) * (1.0 / (1 << 53))
    return state, uniform
