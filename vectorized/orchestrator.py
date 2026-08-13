"""Streaming chunk orchestrator (component D).

`run_millions` never materializes more than one chunk's arrays at a time:
allocate, seed, simulate, fold into StreamingStats, drop the chunk, repeat.
Total resident memory is therefore bounded by `chunk_size * bytes_per_run`,
not by `total_runs` -- the thing that makes ">=1M runs, <=2GB" a chunk-size
choice rather than a total-runs ceiling.
"""

from __future__ import annotations

import gc
import time
from dataclasses import dataclass, field

import numpy as np

from vectorized import crops
from vectorized.config_arrays import VectorConfig, load_vector_config
from vectorized.kernel import simulate_chunk
from vectorized.state import allocate, bytes_per_run, init_runs
from vectorized.stats import StreamingStats

DEFAULT_MAX_CHUNK = 100_000


@dataclass
class BatchResult:
    total_runs: int
    num_plots: int
    num_days: int
    wall_seconds: float
    overall_money: StreamingStats
    overall_harvest: StreamingStats
    # Phase 2 ("storage & spoilage"): informational only -- shadow accounting,
    # doesn't affect overall_money -- see kernel.py's module docstring.
    overall_spoiled: StreamingStats
    overall_storage_cost: StreamingStats
    # Phase 4 ("processing"): units of processed product completed and sold.
    overall_processed: StreamingStats
    # Phase 5 ("contracts", simplified scope): per-run counts/totals.
    overall_contracts_completed: StreamingStats
    overall_contracts_failed: StreamingStats
    overall_contract_penalties: StreamingStats
    # Phase 7 ("upgrades"): how many of the catalog's upgrades a run bought.
    overall_upgrades_owned: StreamingStats
    by_strategy_money: dict = field(default_factory=dict)
    by_strategy_harvest: dict = field(default_factory=dict)

    def summary(self) -> str:
        lines = [
            f"{self.total_runs:,} runs x {self.num_plots} plots x {self.num_days} days "
            f"in {self.wall_seconds:.2f}s ({self.total_runs / self.wall_seconds:,.0f} runs/s)",
            f"  overall money:   mean={self.overall_money.mean:9.2f}  "
            f"stddev={self.overall_money.stddev:8.2f}  "
            f"min={self.overall_money.minimum:9.2f}  max={self.overall_money.maximum:9.2f}",
            f"  overall harvest: mean={self.overall_harvest.mean:9.2f}  "
            f"stddev={self.overall_harvest.stddev:8.2f}",
            f"  overall spoiled: mean={self.overall_spoiled.mean:9.2f}  "
            f"stddev={self.overall_spoiled.stddev:8.2f}  "
            f"(storage cost mean={self.overall_storage_cost.mean:6.2f}, shadow accounting -- "
            f"not deducted from money)",
            f"  overall processed: mean={self.overall_processed.mean:7.2f}  "
            f"stddev={self.overall_processed.stddev:8.2f}",
            f"  overall contracts: completed mean={self.overall_contracts_completed.mean:5.2f}  "
            f"failed mean={self.overall_contracts_failed.mean:5.2f}  "
            f"penalties mean={self.overall_contract_penalties.mean:6.2f}",
            f"  overall upgrades owned: mean={self.overall_upgrades_owned.mean:4.2f}  "
            f"max={self.overall_upgrades_owned.maximum:.0f}",
        ]
        for sid, name in enumerate(crops.STRATEGY_NAMES):
            m = self.by_strategy_money.get(sid)
            h = self.by_strategy_harvest.get(sid)
            if m is None or m.count == 0:
                continue
            lines.append(
                f"  {name:12s} (n={m.count:>8,}): "
                f"money mean={m.mean:9.2f} stddev={m.stddev:8.2f}  "
                f"harvest mean={h.mean:8.2f}"
            )
        return "\n".join(lines)


def choose_chunk_size(
    num_plots: int,
    max_memory_gb: float,
    max_chunk: int = DEFAULT_MAX_CHUNK,
    lots_per_plot: int = 1,
    base_capacity: int = 0,
    num_buyers: int = 0,
    num_upgrades: int = 0,
    total_capacity_bonus: int = 0,
    total_processing_capacity_bonus: int = 0,
) -> int:
    """Chunk size ≤ max_chunk, and small enough that one chunk's arrays fit
    the memory budget (component D step 1).

    `num_plots` is the starting (pre-upgrade) plot count -- Phase 7 grows
    the actual allocated width by `total_capacity_bonus`/
    `total_processing_capacity_bonus` (the max a run could unlock, see
    state.py's docstring), so the memory budget has to be sized off those
    max widths, not the starting ones.
    """
    num_plots_max = num_plots + total_capacity_bonus
    num_job_slots_max = base_capacity + total_processing_capacity_bonus
    num_lot_slots = num_plots_max * lots_per_plot + num_job_slots_max
    per_run = bytes_per_run(
        num_plots_max, num_lot_slots, num_job_slots_max, num_buyers, num_upgrades
    )
    budget_bound = int((max_memory_gb * (1024**3)) // per_run)
    return max(1, min(max_chunk, budget_bound))


def run_millions(
    total_runs: int,
    num_plots: int = 10,
    num_days: int = 365,
    master_seed: int = 42,
    max_memory_gb: float = 2.0,
    max_chunk: int = DEFAULT_MAX_CHUNK,
    strategy_weights: tuple[float, float, float] = (1.0, 1.0, 1.0),
    progress: bool = False,
    config: VectorConfig | None = None,
) -> BatchResult:
    """Simulate `total_runs` runs in memory-bounded streaming chunks.

    `strategy_weights` gives the (greedy, conservative, random) mix; runs
    within each chunk round-robin the roster in that proportion so every
    chunk -- not just the whole batch -- carries a representative mix, which
    keeps `by_strategy_*` stats stable even if a run is interrupted partway.

    `config` is a `config_arrays.VectorConfig` (crop economics, soil
    dynamics, weather, watering/fertilizer settings, all read from the real
    `config/*.json`) -- loaded once and reused by default, since it's the
    same read-only config for every chunk; pass an explicit one (e.g. loaded
    from a different `config_dir`, for an A/B comparison) to override.
    """
    if config is None:
        config = load_vector_config()

    chunk_size = choose_chunk_size(
        num_plots,
        max_memory_gb,
        max_chunk,
        config.lots_per_plot,
        config.base_capacity,
        config.num_buyers,
        config.num_upgrades,
        config.total_capacity_bonus,
        config.total_processing_capacity_bonus,
    )
    # Phase 7: state.py's plot/job-slot dimensions are allocated at their
    # *max* width (base + total_capacity_bonus/total_processing_capacity_bonus)
    # -- see state.py's docstring. `num_plots` stays the starting count this
    # function's callers already know (init_runs needs it to seed active_plots).
    num_plots_max = num_plots + config.total_capacity_bonus
    num_job_slots_max = config.base_capacity + config.total_processing_capacity_bonus
    num_lot_slots = num_plots_max * config.lots_per_plot + num_job_slots_max
    num_buyers = config.num_buyers
    weights = np.asarray(strategy_weights, dtype=np.float64)
    weights = weights / weights.sum()

    overall_money = StreamingStats()
    overall_harvest = StreamingStats()
    overall_spoiled = StreamingStats()
    overall_storage_cost = StreamingStats()
    overall_processed = StreamingStats()
    overall_contracts_completed = StreamingStats()
    overall_contracts_failed = StreamingStats()
    overall_contract_penalties = StreamingStats()
    overall_upgrades_owned = StreamingStats()
    by_money = {sid: StreamingStats() for sid in range(len(crops.STRATEGY_NAMES))}
    by_harvest = {sid: StreamingStats() for sid in range(len(crops.STRATEGY_NAMES))}

    start = time.perf_counter()
    run_offset = 0
    while run_offset < total_runs:
        this_chunk = min(chunk_size, total_runs - run_offset)

        state = allocate(
            this_chunk,
            num_plots_max,
            num_lot_slots,
            num_job_slots_max,
            num_buyers,
            config.num_upgrades,
        )
        # Deterministic strategy assignment: cumulative-weight bucketing of
        # each row's fractional position in [0, 1), not per-row RNG draws --
        # keeps the mix exact and independent of chunk boundaries.
        fractions = (np.arange(this_chunk, dtype=np.float64) + 0.5) / this_chunk
        cum_weights = np.cumsum(weights)
        strategy_of_run = np.searchsorted(cum_weights, fractions).astype(np.int8)
        strategy_of_run = np.clip(strategy_of_run, 0, len(crops.STRATEGY_NAMES) - 1)

        init_runs(state, config, master_seed, run_offset, strategy_of_run, num_plots)
        simulate_chunk(state, num_days, config)

        overall_money.update(state.money)
        overall_harvest.update(state.total_harvest)
        overall_spoiled.update(state.total_spoiled)
        overall_storage_cost.update(state.total_storage_cost)
        overall_processed.update(state.total_processed)
        overall_contracts_completed.update(state.total_contracts_completed)
        overall_contracts_failed.update(state.total_contracts_failed)
        overall_contract_penalties.update(state.total_contract_penalties)
        overall_upgrades_owned.update(state.upgrade_owned.sum(axis=1).astype(np.float64))
        for sid in by_money:
            mask = strategy_of_run == sid
            if mask.any():
                by_money[sid].update(state.money[mask])
                by_harvest[sid].update(state.total_harvest[mask])

        run_offset += this_chunk
        if progress:
            elapsed = time.perf_counter() - start
            rate = run_offset / elapsed if elapsed > 0 else 0.0
            print(f"  {run_offset:,}/{total_runs:,} runs ({rate:,.0f} runs/s)", flush=True)

        # Explicit per the risk-mitigation ask: don't wait for refcounting to
        # get around to a multi-hundred-MB chunk before the next one is
        # allocated -- bound peak RSS to ~2 chunks' worth, not N chunks'.
        del state
        gc.collect()

    wall = time.perf_counter() - start
    return BatchResult(
        total_runs=total_runs,
        num_plots=num_plots,
        num_days=num_days,
        wall_seconds=wall,
        overall_money=overall_money,
        overall_harvest=overall_harvest,
        overall_spoiled=overall_spoiled,
        overall_storage_cost=overall_storage_cost,
        overall_processed=overall_processed,
        overall_contracts_completed=overall_contracts_completed,
        overall_contracts_failed=overall_contracts_failed,
        overall_contract_penalties=overall_contract_penalties,
        overall_upgrades_owned=overall_upgrades_owned,
        by_strategy_money=by_money,
        by_strategy_harvest=by_harvest,
    )


def run_isolated_strategy_fallback(
    strategy_id: int,
    total_runs: int,
    num_plots: int,
    num_days: int,
    master_seed: int,
    config: VectorConfig | None = None,
) -> StreamingStats:
    """Reference pattern for the "can't be vectorized" escape hatch.

    Every strategy in crops.py *is* mask-vectorizable, so nothing calls this
    in the normal run_millions path. It's here so the escape hatch this
    module's docstring/README promises is a real, exercised code path rather
    than a claim: run one strategy through vectorized.reference's scalar
    per-run loop instead of the numba kernel, in chunks small enough to stay
    cheap, folding into the same StreamingStats the vectorized path uses so
    results merge with a mixed-strategy batch's other rows).
    """
    from vectorized.reference import simulate_run_reference

    if config is None:
        config = load_vector_config()

    stats = StreamingStats()
    batch: list[float] = []
    for k in range(total_runs):
        result = simulate_run_reference(config, master_seed, k, strategy_id, num_plots, num_days)
        batch.append(result["money"])
        if len(batch) >= 1000:
            stats.update(np.array(batch, dtype=np.float64))
            batch = []
    if batch:
        stats.update(np.array(batch, dtype=np.float64))
    return stats
