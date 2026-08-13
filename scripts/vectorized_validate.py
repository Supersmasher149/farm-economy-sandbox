#!/usr/bin/env python3
"""Validate vectorized.kernel against vectorized.reference (component E).

For a spread of master seeds, run indices, strategies and plot counts, this
runs a size-1 chunk through the numba kernel and one run through the pure-
Python sequential reference, and asserts they agree to float32 precision.
It also checks the chunk-size-independence property rng.py's docstring
promises: the same global run index produces the same result whether it's
run alone or embedded at various offsets inside a larger chunk. A third
check (Phase 2) forces the storage FEFO capacity-trim and full age-out
spoilage branches to actually fire, via a tiny-capacity config variant, and
confirms kernel vs. reference still agree there too. A fourth check (Phase
4) confirms processing jobs actually start/complete/sell under the default
config -- no forcing needed, it happens naturally within a normal run. A
fifth check (Phase 5) confirms contracts both complete successfully and
fail their deadline under the default config, same "no forcing needed"
reasoning.

This does NOT compare against simulation/'s real engine -- see
vectorized/README.md for why. It validates internal consistency of this
package's own two implementations of the same algorithm (itself a
config-driven port of simulation/crop_growth.py + simulation/weather.py,
per kernel.py's per-block comments -- but still not bit-exact-comparable
to the real engine: different RNG scheme, still-simplified economy).

Usage: python3 scripts/vectorized_validate.py
Needs requirements-fast.txt (numpy, numba) installed.
"""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from vectorized import crops
from vectorized.config_arrays import load_vector_config
from vectorized.kernel import simulate_chunk
from vectorized.reference import simulate_run_reference
from vectorized.state import allocate, init_runs

RTOL = 1e-4  # float32 has ~7 significant decimal digits; this stays well inside that
ATOL = 1e-3  # absolute floor for values that round to ~0

CONFIG = load_vector_config()

# A config variant with a deliberately tiny storage capacity, so
# check_storage_capacity_trim() exercises the FEFO capacity-trim branch (not
# just age-out spoilage) on every check without touching config/storage.json
# -- that file is real balance data, not a test fixture.
TINY_CAPACITY_CONFIG = dataclasses.replace(CONFIG, storage_capacity=np.int32(3))


def _num_lot_slots(num_plots: int, config=CONFIG) -> int:
    return num_plots * config.lots_per_plot + config.base_capacity


def _kernel_single_run(
    master_seed: int, run_index: int, strategy: int, num_plots: int, num_days: int, config=CONFIG
) -> dict:
    state = allocate(
        1, num_plots, _num_lot_slots(num_plots, config), config.base_capacity, config.num_buyers
    )
    init_runs(state, config, master_seed, run_index, np.array([strategy], dtype=np.int8))
    simulate_chunk(state, num_days, config)
    return {
        "money": float(state.money[0]),
        "total_harvest": float(state.total_harvest[0]),
        "total_revenue": float(state.total_revenue[0]),
        "total_spoiled": float(state.total_spoiled[0]),
        "total_storage_cost": float(state.total_storage_cost[0]),
        "total_processed": float(state.total_processed[0]),
        "reputation": float(state.reputation[0]),
        "total_contracts_completed": float(state.total_contracts_completed[0]),
        "total_contracts_failed": float(state.total_contracts_failed[0]),
        "total_contract_penalties": float(state.total_contract_penalties[0]),
    }


def _assert_close(label: str, a: dict, b: dict) -> None:
    for key in (
        "money",
        "total_harvest",
        "total_revenue",
        "total_spoiled",
        "total_storage_cost",
        "total_processed",
        "reputation",
        "total_contracts_completed",
        "total_contracts_failed",
        "total_contract_penalties",
    ):
        if not np.isclose(a[key], b[key], rtol=RTOL, atol=ATOL):
            raise AssertionError(
                f"{label}: kernel {key}={a[key]!r} != reference {key}={b[key]!r} "
                f"(diff={abs(a[key] - b[key])!r})"
            )


def check_kernel_matches_reference(num_days: int) -> int:
    checks = 0
    seeds = [1, 42, 12345, 999_999_937]
    plots = [1, 3, 10]
    for master_seed in seeds:
        for run_index in (0, 1, 7, 1000):
            for strategy in (
                crops.STRATEGY_GREEDY,
                crops.STRATEGY_CONSERVATIVE,
                crops.STRATEGY_RANDOM,
            ):
                for num_plots in plots:
                    kernel_result = _kernel_single_run(
                        master_seed, run_index, strategy, num_plots, num_days
                    )
                    reference_result = simulate_run_reference(
                        CONFIG, master_seed, run_index, strategy, num_plots, num_days
                    )
                    _assert_close(
                        f"seed={master_seed} run={run_index} strat={strategy} plots={num_plots}",
                        kernel_result,
                        reference_result,
                    )
                    checks += 1
    return checks


def check_chunk_size_independence(num_days: int) -> int:
    """The same global run index must give the same result regardless of
    which chunk it's simulated in, or at what offset within that chunk."""
    master_seed = 2026
    num_plots = 5
    strategies = np.array(
        [crops.STRATEGY_GREEDY, crops.STRATEGY_CONSERVATIVE, crops.STRATEGY_RANDOM] * 20,
        dtype=np.int8,
    )
    target_global_index = 37  # picked to land in the middle of a chunk below

    # Baseline: alone, in a size-1 chunk at its own global offset.
    baseline_state = allocate(
        1, num_plots, _num_lot_slots(num_plots), CONFIG.base_capacity, CONFIG.num_buyers
    )
    init_runs(
        baseline_state,
        CONFIG,
        master_seed,
        target_global_index,
        strategies[target_global_index : target_global_index + 1],
    )
    simulate_chunk(baseline_state, num_days, CONFIG)
    baseline = {
        "money": float(baseline_state.money[0]),
        "total_harvest": float(baseline_state.total_harvest[0]),
        "total_revenue": float(baseline_state.total_revenue[0]),
        "total_spoiled": float(baseline_state.total_spoiled[0]),
        "total_storage_cost": float(baseline_state.total_storage_cost[0]),
        "total_processed": float(baseline_state.total_processed[0]),
        "reputation": float(baseline_state.reputation[0]),
        "total_contracts_completed": float(baseline_state.total_contracts_completed[0]),
        "total_contracts_failed": float(baseline_state.total_contracts_failed[0]),
        "total_contract_penalties": float(baseline_state.total_contract_penalties[0]),
    }

    checks = 0
    for chunk_size, offset in [(60, 0), (40, 0), (10, 30)]:
        if not (offset <= target_global_index < offset + chunk_size):
            continue
        state = allocate(
            chunk_size,
            num_plots,
            _num_lot_slots(num_plots),
            CONFIG.base_capacity,
            CONFIG.num_buyers,
        )
        init_runs(state, CONFIG, master_seed, offset, strategies[offset : offset + chunk_size])
        simulate_chunk(state, num_days, CONFIG)
        local_index = target_global_index - offset
        result = {
            "money": float(state.money[local_index]),
            "total_harvest": float(state.total_harvest[local_index]),
            "total_revenue": float(state.total_revenue[local_index]),
            "total_spoiled": float(state.total_spoiled[local_index]),
            "total_storage_cost": float(state.total_storage_cost[local_index]),
            "total_processed": float(state.total_processed[local_index]),
            "reputation": float(state.reputation[local_index]),
            "total_contracts_completed": float(state.total_contracts_completed[local_index]),
            "total_contracts_failed": float(state.total_contracts_failed[local_index]),
            "total_contract_penalties": float(state.total_contract_penalties[local_index]),
        }
        _assert_close(f"chunk_size={chunk_size} offset={offset}", result, baseline)
        checks += 1
    if checks == 0:
        raise AssertionError(
            "no chunk configuration covered the target run index -- test is broken"
        )
    return checks


def check_storage_capacity_trim(num_days: int) -> int:
    """Phase 2: force both the FEFO capacity-trim branch and full age-out
    spoilage to actually execute (not just be numerically dormant), and
    check kernel vs. reference agree on the resulting counters.

    `simulate_chunk` itself raises loudly if any run overflows its
    lot-slot bound (see kernel.py's overflow_events check) -- reaching the
    assertions below without an exception already proves that invariant
    held for every one of these runs, on top of the storage_capacity=3
    config forcing trims well before the default capacity=100 would.

    Individual (seed, strategy, plot-count) combinations can still
    legitimately harvest nothing over the run (a genuinely unlucky loss-roll
    streak, or an unaffordable strategy on a single plot) -- that's not a
    storage bug, just variance, so the "the branch actually fired" check is
    an OR across the whole grid rather than a per-combination requirement.
    """
    checks = 0
    any_spoiled = False
    seeds = [1, 42, 12345]
    plots = [1, 3, 10]
    for master_seed in seeds:
        for num_plots in plots:
            for strategy in (
                crops.STRATEGY_GREEDY,
                crops.STRATEGY_CONSERVATIVE,
                crops.STRATEGY_RANDOM,
            ):
                kernel_result = _kernel_single_run(
                    master_seed, 0, strategy, num_plots, num_days, config=TINY_CAPACITY_CONFIG
                )
                reference_result = simulate_run_reference(
                    TINY_CAPACITY_CONFIG, master_seed, 0, strategy, num_plots, num_days
                )
                label = f"seed={master_seed} strat={strategy} plots={num_plots} (tiny capacity)"
                _assert_close(label, kernel_result, reference_result)
                any_spoiled = any_spoiled or kernel_result["total_spoiled"] > 0.0
                checks += 1
    if not any_spoiled:
        raise AssertionError(
            "check_storage_capacity_trim: total_spoiled was 0 across the entire grid -- "
            "the capacity-trim/age-out branch never actually fired, this check isn't "
            "exercising the code path it claims to"
        )
    return checks


def check_processing_occurs(num_days: int) -> int:
    """Phase 4: confirm processing jobs actually start, complete, and get
    sold under the default config -- not just numerically dormant -- and
    check kernel vs. reference agree on the resulting `total_processed`.

    Unlike `check_storage_capacity_trim`, no forced config variant is
    needed here: both shipped recipes are cheap and gated by inventory
    every fixed strategy routinely harvests, so processing fires within a
    normal-length run under the real `config/processing.json` values (see
    the module-level check below, and the "OR across the whole grid"
    rationale on `check_storage_capacity_trim`, which applies the same way
    here -- an unlucky individual (seed, strategy, plot-count) combination
    proves nothing on its own).
    """
    checks = 0
    any_processed = False
    seeds = [1, 42, 12345]
    plots = [1, 3, 10]
    for master_seed in seeds:
        for num_plots in plots:
            for strategy in (
                crops.STRATEGY_GREEDY,
                crops.STRATEGY_CONSERVATIVE,
                crops.STRATEGY_RANDOM,
            ):
                kernel_result = _kernel_single_run(master_seed, 0, strategy, num_plots, num_days)
                reference_result = simulate_run_reference(
                    CONFIG, master_seed, 0, strategy, num_plots, num_days
                )
                label = f"seed={master_seed} strat={strategy} plots={num_plots}"
                _assert_close(label, kernel_result, reference_result)
                any_processed = any_processed or kernel_result["total_processed"] > 0.0
                checks += 1
    if not any_processed:
        raise AssertionError(
            "check_processing_occurs: total_processed was 0 across the entire grid -- "
            "the processing start/complete/sell path never actually fired, this check "
            "isn't exercising the code path it claims to"
        )
    return checks


def check_contracts_occur(num_days: int) -> int:
    """Phase 5: confirm contracts actually get offered/accepted/delivered
    *and* fail their deadline under the default config -- not just
    numerically dormant -- and check kernel vs. reference agree on the
    resulting counters.

    Both outcomes matter here, unlike Phase 4's single `total_processed`
    signal: `total_contracts_completed` proves the offer/accept/deliver
    path fires, `total_contracts_failed` (and `total_contract_penalties`
    following it) proves the simplified accept policy -- accept on any
    current stock, not full forecasted coverage -- actually produces
    contracts the farm can't finish in time, the behavior this phase's
    simplification is expected to make *more* likely than the real
    engine's forecast-gated accept, not less.
    """
    checks = 0
    any_completed = False
    any_failed = False
    seeds = [1, 42, 12345]
    plots = [1, 3, 10]
    for master_seed in seeds:
        for num_plots in plots:
            for strategy in (
                crops.STRATEGY_GREEDY,
                crops.STRATEGY_CONSERVATIVE,
                crops.STRATEGY_RANDOM,
            ):
                kernel_result = _kernel_single_run(master_seed, 0, strategy, num_plots, num_days)
                reference_result = simulate_run_reference(
                    CONFIG, master_seed, 0, strategy, num_plots, num_days
                )
                label = f"seed={master_seed} strat={strategy} plots={num_plots}"
                _assert_close(label, kernel_result, reference_result)
                any_completed = any_completed or kernel_result["total_contracts_completed"] > 0.0
                any_failed = any_failed or kernel_result["total_contracts_failed"] > 0.0
                checks += 1
    if not any_completed:
        raise AssertionError(
            "check_contracts_occur: total_contracts_completed was 0 across the entire "
            "grid -- the offer/accept/deliver path never actually fired, this check "
            "isn't exercising the code path it claims to"
        )
    if not any_failed:
        raise AssertionError(
            "check_contracts_occur: total_contracts_failed was 0 across the entire "
            "grid -- the deadline-penalty path never actually fired, this check isn't "
            "exercising the code path it claims to"
        )
    return checks


def main() -> int:
    num_days = 90  # short run: enough days to exercise every branch repeatedly, still fast
    print("Checking kernel vs. sequential reference...")
    n1 = check_kernel_matches_reference(num_days)
    print(f"  {n1} (seed, run, strategy, plot-count) combinations matched within tolerance")

    print("Checking chunk-size independence...")
    n2 = check_chunk_size_independence(num_days)
    print(f"  {n2} chunk configurations agreed on the same global run index")

    print("Checking storage capacity trim + spoilage...")
    n3 = check_storage_capacity_trim(num_days)
    print(f"  {n3} (seed, strategy, plot-count) combinations matched with spoilage confirmed")

    print("Checking processing jobs occur...")
    n4 = check_processing_occurs(num_days)
    print(f"  {n4} (seed, strategy, plot-count) combinations matched with processing confirmed")

    print("Checking contracts occur...")
    n5 = check_contracts_occur(num_days)
    print(f"  {n5} (seed, strategy, plot-count) combinations matched with contracts confirmed")

    print(f"\nOK: {n1 + n2 + n3 + n4 + n5} checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
