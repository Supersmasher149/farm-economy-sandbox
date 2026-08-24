#!/usr/bin/env python3
"""Benchmark vectorized.orchestrator.run_millions vs. the existing engine.

Measures runs/sec at a few chunk sizes, extrapolates to the >=1M-runs-under-
60s-CPU target from the prompt, and reports peak RSS (`resource`, stdlib --
no psutil dependency). Optionally times `main.py batch` for a wall-clock
comparison against the existing process-pool engine.

That comparison is apples-to-oranges on what's being simulated -- this
package's crops.py is a 3-crop illustrative model, not simulation/'s full
config-driven economy (contracts, processing, markets, upgrades) -- so it's
reported as "same wall-clock question, different economic models," not as
a claim that the vectorized path makes the existing engine 1 obsolete.
See vectorized/README.md.

Usage:
  python3 scripts/vectorized_benchmark.py
  python3 scripts/vectorized_benchmark.py --compare-existing-engine
"""

from __future__ import annotations

import argparse
import resource
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vectorized.orchestrator import run_millions

REPO_ROOT = Path(__file__).resolve().parent.parent


def peak_rss_mb() -> float:
    # ru_maxrss is bytes on macOS, KB on Linux -- normalize to MB either way.
    raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return raw / 1024 / 1024 if sys.platform == "darwin" else raw / 1024


def bench_vectorized(total_runs: int, num_plots: int, num_days: int) -> None:
    print(f"\n-- vectorized: {total_runs:,} runs x {num_plots} plots x {num_days} days --")
    # Warm-up call: numba compiles simulate_chunk on first invocation; that
    # compile time is a one-time cost, not part of the runs/sec figure.
    warmup_start = time.perf_counter()
    run_millions(total_runs=8, num_plots=num_plots, num_days=2, master_seed=1)
    print(f"  (numba warm-up/compile: {time.perf_counter() - warmup_start:.2f}s, excluded below)")

    result = run_millions(
        total_runs=total_runs, num_plots=num_plots, num_days=num_days, master_seed=42
    )
    print(f"  {result.summary()}")
    print(f"  peak RSS so far: {peak_rss_mb():.0f} MB")

    projected_1m = 1_000_000 / (total_runs / result.wall_seconds)
    print(f"  projected time for 1,000,000 runs at this rate: {projected_1m:.1f}s")


def bench_existing_engine(runs: int, days: int) -> None:
    print(f"\n-- existing engine (main.py batch): {runs} runs x {days} days, all strategies --")
    cmd = [
        sys.executable,
        str(REPO_ROOT / "main.py"),
        "batch",
        "--runs",
        str(runs),
        "--days",
        str(days),
        "--no-charts",
        "--no-progress",
        "--seed",
        "42",
    ]
    start = time.perf_counter()
    subprocess.run(cmd, cwd=REPO_ROOT, check=True, capture_output=True)
    wall = time.perf_counter() - start
    # main.py batch runs every registered strategy `runs` times each.
    import main as main_module  # local import: only needed for this comparison

    total = runs * len(main_module.AGENT_REGISTRY)
    print(f"  {total:,} total runs (all strategies) in {wall:.2f}s ({total / wall:,.0f} runs/s)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--num-plots", type=int, default=10)
    parser.add_argument("--num-days", type=int, default=365)
    parser.add_argument(
        "--sizes",
        type=int,
        nargs="+",
        default=[1_000, 20_000, 100_000],
        help="chunk-scale run counts to benchmark",
    )
    parser.add_argument(
        "--compare-existing-engine",
        action="store_true",
        help="also time main.py batch for a wall-clock reference point (different economic model)",
    )
    parser.add_argument(
        "--existing-engine-runs",
        type=int,
        default=200,
        help="--runs value passed to main.py batch when --compare-existing-engine is set",
    )
    args = parser.parse_args()

    for size in args.sizes:
        bench_vectorized(size, args.num_plots, args.num_days)

    if args.compare_existing_engine:
        bench_existing_engine(args.existing_engine_runs, args.num_days)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
