#!/usr/bin/env python3
"""Validate vectorized.kernel against vectorized.reference (component E).

For a spread of master seeds, run indices, strategies and plot counts, this
runs a size-1 chunk through the numba kernel and one run through the pure-
Python sequential reference, and asserts they agree to float32 precision.
It also checks the chunk-size-independence property rng.py's docstring
promises: the same global run index produces the same result whether it's
run alone or embedded at various offsets inside a larger chunk.

This does NOT compare against simulation/'s real engine -- see
vectorized/README.md for why. It validates internal consistency of this
package's own two implementations of the same algorithm.

Usage: python3 scripts/vectorized_validate.py
Needs requirements-fast.txt (numpy, numba) installed.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from vectorized import crops
from vectorized.kernel import simulate_chunk
from vectorized.reference import simulate_run_reference
from vectorized.state import allocate, init_runs

RTOL = 1e-4  # float32 has ~7 significant decimal digits; this stays well inside that
ATOL = 1e-3  # absolute floor for values that round to ~0


def _kernel_single_run(
    master_seed: int, run_index: int, strategy: int, num_plots: int, num_days: int
) -> dict:
    state = allocate(1, num_plots)
    init_runs(state, master_seed, run_index, np.array([strategy], dtype=np.int8))
    simulate_chunk(state, num_days)
    return {"money": float(state.money[0]), "total_harvest": float(state.total_harvest[0])}


def _assert_close(label: str, a: dict, b: dict) -> None:
    for key in ("money", "total_harvest"):
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
                        master_seed, run_index, strategy, num_plots, num_days
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
    baseline_state = allocate(1, num_plots)
    init_runs(
        baseline_state,
        master_seed,
        target_global_index,
        strategies[target_global_index : target_global_index + 1],
    )
    simulate_chunk(baseline_state, num_days)
    baseline = {
        "money": float(baseline_state.money[0]),
        "total_harvest": float(baseline_state.total_harvest[0]),
    }

    checks = 0
    for chunk_size, offset in [(60, 0), (40, 0), (10, 30)]:
        if not (offset <= target_global_index < offset + chunk_size):
            continue
        state = allocate(chunk_size, num_plots)
        init_runs(state, master_seed, offset, strategies[offset : offset + chunk_size])
        simulate_chunk(state, num_days)
        local_index = target_global_index - offset
        result = {
            "money": float(state.money[local_index]),
            "total_harvest": float(state.total_harvest[local_index]),
        }
        _assert_close(f"chunk_size={chunk_size} offset={offset}", result, baseline)
        checks += 1
    if checks == 0:
        raise AssertionError(
            "no chunk configuration covered the target run index -- test is broken"
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

    print(f"\nOK: {n1 + n2} checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
