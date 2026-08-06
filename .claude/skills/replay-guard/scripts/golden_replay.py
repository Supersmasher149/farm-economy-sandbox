#!/usr/bin/env python3
"""Golden-replay determinism check for farm-economy-sandbox.

Runs every registered strategy against a fixed set of seeds through the same
`run_single` entry point `main.py replay` uses, and either records the
resulting stats as a baseline (`capture`) or diffs the current stats against
a previously committed baseline (`check`).

Usage:
    python3 golden_replay.py capture
    python3 golden_replay.py check

See ../SKILL.md for when/why to run this.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
BASELINE_PATH = SCRIPT_DIR.parent / "golden_baseline.json"
REPO_ROOT = SCRIPT_DIR.parents[3]

sys.path.insert(0, str(REPO_ROOT))

from main import AGENT_REGISTRY, load_config  # noqa: E402
from runner.single_run import run_single  # noqa: E402

# Seeds already meaningful elsewhere in this repo (test suite / README
# examples), spread across every registered strategy for broader coverage
# than the two hardcoded seeds pytest checks.
GOLDEN_SEEDS = (1, 42, 777, 123456789)

# Fields test_same_seed_produces_identical_results (tests/test_engine.py)
# already asserts on for a single seed; checked here per (strategy, seed).
TRACKED_FIELDS = (
    "money",
    "total_revenue",
    "crop_plant_counts",
    "bankrupt",
    "total_crops_lost",
)


def _run_one(strategy_name: str, seed: int) -> dict:
    crops, upgrades, config, world = load_config()
    watering_settings, fertilizer_config = world["watering"], world["fertilizer"]
    agent = AGENT_REGISTRY[strategy_name]()
    player, _resolved_seed, _history = run_single(
        config,
        agent,
        crops,
        upgrades,
        watering_settings,
        fertilizer_config,
        seed=seed,
        record_history=False,
        world=world,
    )
    return {
        "money": round(player.money, 6),
        "total_revenue": round(player.total_revenue, 6),
        "crop_plant_counts": dict(player.crop_plant_counts),
        "bankrupt": player.bankrupt,
        "total_crops_lost": player.total_crops_lost,
    }


def _run_all() -> dict:
    results = {}
    for strategy_name in sorted(AGENT_REGISTRY):
        for seed in GOLDEN_SEEDS:
            key = f"{strategy_name}:{seed}"
            results[key] = _run_one(strategy_name, seed)
    return results


def cmd_capture(_args: argparse.Namespace) -> int:
    results = _run_all()
    BASELINE_PATH.write_text(json.dumps(results, indent=2, sort_keys=True) + "\n")
    print(
        f"Captured baseline for {len(results)} (strategy, seed) combos "
        f"-> {BASELINE_PATH.relative_to(REPO_ROOT)}"
    )
    return 0


def cmd_check(_args: argparse.Namespace) -> int:
    if not BASELINE_PATH.exists():
        print(f"No baseline found at {BASELINE_PATH}. Run `capture` first.")
        return 2

    baseline = json.loads(BASELINE_PATH.read_text())
    current = _run_all()

    failures = []
    for key in sorted(set(baseline) | set(current)):
        if key not in baseline:
            failures.append((key, "missing from baseline (new strategy/seed since capture)"))
            continue
        if key not in current:
            failures.append((key, "missing from current run (strategy removed?)"))
            continue
        if baseline[key] != current[key]:
            diffs = []
            for field in TRACKED_FIELDS:
                old, new = baseline[key].get(field), current[key].get(field)
                if old != new:
                    diffs.append(f"{field}: {old!r} -> {new!r}")
            failures.append((key, "; ".join(diffs)))

    total = len(set(baseline) | set(current))
    if not failures:
        print(f"PASS: all {total} (strategy, seed) combos match the committed baseline.")
        return 0

    print(f"FAIL: {len(failures)}/{total} combos diverged from the committed baseline:\n")
    for key, detail in failures:
        print(f"  {key}: {detail}")
    print(
        "\nIf this is an intended behavior change, re-run `capture` and commit "
        "the updated golden_baseline.json alongside the change. Otherwise this "
        "is a determinism regression -- see SKILL.md."
    )
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("capture", help="Record current output as the committed baseline.")
    subparsers.add_parser("check", help="Diff current output against the committed baseline.")

    args = parser.parse_args()
    if args.command == "capture":
        return cmd_capture(args)
    return cmd_check(args)


if __name__ == "__main__":
    raise SystemExit(main())
