"""BatchState: the Structure-of-Arrays data contract (component A).

One flat array per field, shape (B,) for run-level fields and (B, P) for
plot-level fields, B = runs in this chunk, P = plots per run. No per-run
Python object, no daily history -- a chunk's total footprint is
`bytes_per_run(P) * B` regardless of num_days, which is what lets
`run_millions` size chunks off a memory budget instead of a run count.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from vectorized import crops


@dataclass
class BatchState:
    money: np.ndarray  # float32 (B,)
    total_harvest: np.ndarray  # float32 (B,)
    strategy_id: np.ndarray  # int8 (B,)
    moisture: np.ndarray  # float32 (B, P)
    nitrogen: np.ndarray  # float32 (B, P)
    crop_type: np.ndarray  # int8 (B, P), -1 == empty
    growth_stage: np.ndarray  # int8 (B, P)
    days_to_harvest: np.ndarray  # int16 (B, P)
    # Per-(run) and per-(run, plot) splitmix64 stream states -- not part of
    # the spec's field list, but they're what make a chunk resumable /
    # chunk-size-independent, so they travel with the rest of the SoA rather
    # than living in a separate object. See vectorized/rng.py.
    rng_run_state: np.ndarray  # uint64 (B,)
    rng_plot_state: np.ndarray  # uint64 (B, P)

    @property
    def num_runs(self) -> int:
        return self.money.shape[0]

    @property
    def num_plots(self) -> int:
        return self.moisture.shape[1]


# Bytes/run this layout costs, used by run_millions to size chunks against a
# memory budget. Keep in sync with the dataclass fields above by hand -- it's
# a closed-form sum, not introspected, so it stays exact even though numpy
# dtype itemsizes aren't literally hardcoded in two places by accident.
def bytes_per_run(num_plots: int) -> int:
    per_run = 4 + 4 + 1 + 8  # money f4, total_harvest f4, strategy_id i1, rng_run u8
    per_plot = 4 + 4 + 1 + 1 + 2 + 8  # moisture, nitrogen, crop_type, growth_stage, dth, rng
    return per_run + num_plots * per_plot


def allocate(num_runs: int, num_plots: int) -> BatchState:
    """Allocate an uninitialized chunk. Call `init_runs` before simulating."""
    return BatchState(
        money=np.empty(num_runs, dtype=np.float32),
        total_harvest=np.empty(num_runs, dtype=np.float32),
        strategy_id=np.empty(num_runs, dtype=np.int8),
        moisture=np.empty((num_runs, num_plots), dtype=np.float32),
        nitrogen=np.empty((num_runs, num_plots), dtype=np.float32),
        crop_type=np.empty((num_runs, num_plots), dtype=np.int8),
        growth_stage=np.empty((num_runs, num_plots), dtype=np.int8),
        days_to_harvest=np.empty((num_runs, num_plots), dtype=np.int16),
        rng_run_state=np.empty(num_runs, dtype=np.uint64),
        rng_plot_state=np.empty((num_runs, num_plots), dtype=np.uint64),
    )


def init_runs(
    state: BatchState,
    master_seed: int,
    run_index_offset: int,
    strategy_of_run: np.ndarray,
) -> None:
    """Initialize a freshly-allocated chunk in place.

    `run_index_offset` is the global run index of row 0 in this chunk -- the
    thing that makes seeding independent of chunk size and chunk position
    (see vectorized/rng.py's module docstring and
    scripts/vectorized_validate.py, which checks exactly this).
    `strategy_of_run` assigns each row a strategy id (component C);
    `run_millions` round-robins the roster across a chunk.
    """
    num_runs, num_plots = state.num_runs, state.num_plots
    global_index = run_index_offset + np.arange(num_runs, dtype=np.int64)

    # Per-run weather stream seed: one splitmix64 mix of (master_seed + k),
    # vectorized. Must match vectorized.rng.run_seed's scalar arithmetic
    # exactly, element by element -- see vectorized/rng.py and
    # scripts/vectorized_validate.py.
    from vectorized import rng as _rng

    x = (np.uint64(master_seed) + global_index.astype(np.uint64)) & np.uint64(_rng.MASK64)
    z = (x + np.uint64(_rng.GOLDEN_GAMMA)) & np.uint64(_rng.MASK64)
    z = (z ^ (z >> np.uint64(30))) * np.uint64(_rng.MIX_MULT_1)
    z = (z ^ (z >> np.uint64(27))) * np.uint64(_rng.MIX_MULT_2)
    run_state = z ^ (z >> np.uint64(31))
    state.rng_run_state[:] = run_state

    plot_index = np.arange(num_plots, dtype=np.uint64)
    salted = run_state[:, None] ^ (plot_index[None, :] * np.uint64(_rng.PLOT_SALT))
    salted &= np.uint64(_rng.MASK64)
    pz = (salted + np.uint64(_rng.GOLDEN_GAMMA)) & np.uint64(_rng.MASK64)
    pz = (pz ^ (pz >> np.uint64(30))) * np.uint64(_rng.MIX_MULT_1)
    pz = (pz ^ (pz >> np.uint64(27))) * np.uint64(_rng.MIX_MULT_2)
    state.rng_plot_state[:, :] = pz ^ (pz >> np.uint64(31))

    state.money[:] = crops.STARTING_MONEY
    state.total_harvest[:] = 0.0
    state.strategy_id[:] = strategy_of_run
    state.moisture[:, :] = crops.STARTING_MOISTURE
    state.nitrogen[:, :] = crops.STARTING_NITROGEN
    state.crop_type[:, :] = -1
    state.growth_stage[:, :] = 0
    state.days_to_harvest[:, :] = 0
