#!/usr/bin/env python3
"""Statistical sampling profiler for the batch simulation path.

Why this exists rather than `python3 -m cProfile main.py batch ...`:

- **cProfile lies about this codebase.** A batch makes ~85M Python calls per
  440 runs, so per-call instrumentation dominates and inflates wall time ~4x.
  Worse, it inflates it *unevenly* -- functions with many cheap calls
  (`_clamp`, `is_crop_unlocked`) look far hotter than they are, which is
  exactly the signal an optimization decision hinges on.
- **Stub-based ablation lies too.** Replacing a hot function with a no-op to
  measure its share collapses the economy: stubbing
  `markets.update_daily_prices` takes a batch from 133,320 simulated days to
  8,578, so the "share" measured is of a completely different workload.

Sampling has neither problem: it does not change what the simulation does,
and it is free of call-count bias. Frames are sampled off the worker thread
on a fixed interval; SELF time is attributed to the top frame, CUMULATIVE
time to every frame on the stack.

Usage:
    python3 tools/sample_profile.py                  # default 200 runs/strategy
    python3 tools/sample_profile.py --runs 50        # quicker, noisier
    python3 tools/sample_profile.py --json out.json  # machine-readable

Statistical error is reported alongside the results -- treat differences
smaller than that as noise. Compare two runs by their `ns/sim-day`, which
normalizes away any change in how long runs survive.
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import sys
import threading
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from main import AGENT_REGISTRY, load_config  # noqa: E402
from runner.batch_run import run_batch  # noqa: E402

# Buckets exist to answer one question: how much of the runtime is even
# *eligible* to be moved into a compiled extension? Agent decision logic is
# not -- the project's "agents decide, the engine mutates" separation
# requires it stay in Python.
KERNEL_MODULES = frozenset({"simulation/crop_growth.py", "simulation/weather.py"})
KERNEL_FUNCTIONS = frozenset(
    {
        "simulation/markets.py:quote",
        "simulation/markets.py:update_daily_prices",
        "simulation/inventory.py:age_and_spoil",
        "simulation/economy_rules.py:soil_health_factor",
        "simulation/economy_rules.py:effective_growth_days",
        "simulation/economy_rules.py:is_crop_unlocked",
    }
)

BUCKET_KERNEL = "numeric kernel (portable to C)"
BUCKET_GLUE = "engine glue / state mutation"
BUCKET_AGENT = "agent decision logic (must stay Python)"
BUCKET_OTHER = "other (runner, metrics, stdlib)"


class Sampler:
    """Samples one thread's stack on a timer. Daemon thread, no side effects."""

    def __init__(self, interval: float = 0.001):
        self.interval = interval
        self.self_time: collections.Counter = collections.Counter()
        self.cumulative: collections.Counter = collections.Counter()
        self.buckets: collections.Counter = collections.Counter()
        self.total = 0
        self._target_tid: int | None = None
        self._running = False
        self._thread: threading.Thread | None = None

    @staticmethod
    def _label(frame) -> str:
        code = frame.f_code
        filename = os.path.relpath(code.co_filename, REPO_ROOT)
        return f"{filename}:{code.co_name}"

    @staticmethod
    def _bucket(label: str) -> str:
        module = label.rsplit(":", 1)[0]
        if module in KERNEL_MODULES or label in KERNEL_FUNCTIONS:
            return BUCKET_KERNEL
        if module.startswith("agents/"):
            return BUCKET_AGENT
        if module.startswith("simulation/"):
            return BUCKET_GLUE
        return BUCKET_OTHER

    def _loop(self) -> None:
        while self._running:
            frame = sys._current_frames().get(self._target_tid)
            if frame is not None:
                self.total += 1
                top = self._label(frame)
                self.self_time[top] += 1
                self.buckets[self._bucket(top)] += 1
                # A recursive frame must not be counted twice for the same
                # sample, or cumulative shares exceed 100%.
                seen = set()
                while frame is not None:
                    label = self._label(frame)
                    if label not in seen:
                        seen.add(label)
                        self.cumulative[label] += 1
                    frame = frame.f_back
            time.sleep(self.interval)

    def start(self, target_tid: int) -> None:
        self._target_tid = target_tid
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=1.0)

    @property
    def error_pct(self) -> float:
        """Rough +-1 sigma absolute error on a reported percentage."""
        return 100 * (0.5 / self.total**0.5) if self.total else float("inf")


def run_workload(runs: int, seed: int, state: dict) -> None:
    crops, upgrades, config, world = load_config()
    agents = [cls() for cls in AGENT_REGISTRY.values()]
    count = days = 0
    for result in run_batch(
        config,
        agents,
        crops,
        upgrades,
        world["watering"],
        world["fertilizer"],
        num_runs=runs,
        base_seed=seed,
        # workers=1 deliberately: sampling a process pool would only ever see
        # the parent process blocking on IPC. Sequential throughput is also
        # the cleaner number to compare across changes.
        world=world,
        workers=1,
    ):
        count += 1
        days += result.days_simulated
    state["runs"] = count
    state["sim_days"] = days
    state["strategies"] = len(agents)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--runs", type=int, default=200, help="runs per strategy (default 200)")
    parser.add_argument("--seed", type=int, default=777, help="batch base seed (default 777)")
    parser.add_argument("--interval", type=float, default=0.001, help="sample interval seconds")
    parser.add_argument("--top", type=int, default=25, help="functions to list (default 25)")
    parser.add_argument("--json", metavar="PATH", help="also write results as JSON")
    args = parser.parse_args()

    state: dict = {}
    worker = threading.Thread(target=run_workload, args=(args.runs, args.seed, state))
    sampler = Sampler(args.interval)

    worker.start()
    started = time.perf_counter()
    sampler.start(worker.ident)
    worker.join()
    wall = time.perf_counter() - started
    sampler.stop()

    total = sampler.total
    if not total:
        print("No samples collected -- workload finished too fast. Raise --runs.")
        return 1

    sim_days = state["sim_days"]
    ns_per_day = wall / sim_days * 1e9
    runs_per_s = state["runs"] / wall

    print(
        f"\n{state['runs']} runs ({state['strategies']} strategies x {args.runs}), "
        f"{sim_days} sim-days, seed {args.seed}"
    )
    print(
        f"wall {wall:.1f}s | {runs_per_s:.0f} runs/s | {ns_per_day:.0f} ns/sim-day | "
        f"{total} samples (+-{sampler.error_pct:.2f}% abs)\n"
    )

    print("=== SELF time by bucket ===")
    for name, count in sampler.buckets.most_common():
        print(f"  {100 * count / total:5.1f}%  {name}")

    print(f"\n=== SELF time, top {args.top} ===")
    for name, count in sampler.self_time.most_common(args.top):
        print(f"  {100 * count / total:5.1f}%  {name}")

    print(f"\n=== CUMULATIVE time, top {args.top} ===")
    for name, count in sampler.cumulative.most_common(args.top):
        print(f"  {100 * count / total:5.1f}%  {name}")

    kernel = sampler.buckets[BUCKET_KERNEL] / total
    print(f"\n=== Amdahl ceiling for a compiled kernel ({100 * kernel:.1f}% of self time) ===")
    for speedup in (2, 5, 10, 50):
        print(
            f"  kernel {speedup:2d}x faster -> whole-sim {1 / ((1 - kernel) + kernel / speedup):.2f}x"
        )
    print(f"  kernel infinitely fast -> whole-sim {1 / (1 - kernel):.2f}x")

    if args.json:
        payload = {
            "wall_seconds": wall,
            "runs": state["runs"],
            "sim_days": sim_days,
            "runs_per_second": runs_per_s,
            "ns_per_sim_day": ns_per_day,
            "samples": total,
            "error_pct": sampler.error_pct,
            "buckets": dict(sampler.buckets),
            "self_time": dict(sampler.self_time),
            "cumulative": dict(sampler.cumulative),
        }
        with open(args.json, "w") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
        print(f"\nWrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
