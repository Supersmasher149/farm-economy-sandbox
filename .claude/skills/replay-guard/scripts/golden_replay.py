#!/usr/bin/env python3
"""Golden-replay determinism check for farm-economy-sandbox.

Runs every registered strategy against a fixed set of seeds through the same
`run_single` entry point `main.py replay` uses, and either records the
resulting stats as a baseline (`capture`) or diffs the current stats against
a previously committed baseline (`check`).

Usage:
    python3 golden_replay.py capture
    python3 golden_replay.py check
    python3 golden_replay.py trace <strategy> <seed>

Floats are recorded as `float.hex()`, never rounded. Rounding to 6dp -- which
this script used to do -- tolerates roughly 275,000 ulps on a five-figure money
value, so it would pass a build whose arithmetic had already drifted, right up
until the drift crossed an affordability `>=` or a quality-grade threshold and
flipped a discrete outcome. Hex also distinguishes +0.0 from -0.0, which `==`
does not, and the literal max/min forms in simulation/crop_growth.py exist
specifically to preserve that distinction.

Each combo also carries a `trajectory` digest: a blake2b chained over every
simulated day's state, not just the final tally. Without it a divergence that
cancels out by the last day is invisible. `trace` prints the per-day digests
for one combo so a failure bisects to the first day that moved.

See ../SKILL.md for when/why to run this.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
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

# Every field below is already on PlayerState, so widening the net costs
# nothing but catches drift the original five (money, total_revenue,
# crop_plant_counts, bankrupt, total_crops_lost) would miss -- a run can end on
# the same money having taken a different route to get there.
TRACKED_FIELDS = (
    "money",
    "total_revenue",
    "total_expenses",
    "reputation",
    "processing_revenue",
    "contract_penalties",
    "lowest_money",
    "highest_money",
    "bankrupt",
    "bankruptcy_day",
    "bankruptcy_reason",
    "total_planted",
    "total_harvested",
    "total_sold",
    "total_spoiled",
    "total_processed",
    "total_crops_lost",
    "total_waterings",
    "total_harvest_events",
    "total_fertilizer_bought",
    "total_fertilizer_applied",
    "contracts_completed",
    "contracts_failed",
    "idle_days",
    "slot_days",
    "occupied_slot_days",
    "crop_plant_counts",
    "quality_harvested",
    "losses_by_cause",
    "seed_inventory",
    "crop_inventory",
    "expenses_by_category",
    "revenue_by_channel",
    "upgrades_owned",
    "trajectory",
)

# Per-plot soil scalars folded into the daily trajectory digest. Named
# explicitly rather than derived from dataclasses.fields(PlotState) so adding a
# field to PlotState cannot silently invalidate every committed baseline -- if
# a new field belongs in the digest, it gets added here deliberately.
PLOT_FIELDS = (
    "moisture",
    "nitrogen",
    "phosphorus",
    "potassium",
    "ph",
    "soil_health",
    "pest_pressure",
    "disease_pressure",
)

STRESS_FIELDS = (
    "water_stress",
    "nutrient_stress",
    "temperature_stress",
    "pest_stress",
    "disease_stress",
)


def _exact(value):
    """JSON-encodable exact representation of a simulation value.

    Floats become `float.hex()` strings; ints, bools, strings and None stay
    JSON-native. That split is deliberate: it makes a float/int type change
    (0.0 -> 0) show up as a changed baseline rather than comparing equal, which
    matters because PlayerState.decision_random hashes `repr()` and so is
    sensitive to the exact type of what it is handed.
    """
    if isinstance(value, float):
        return value.hex()
    if isinstance(value, dict):
        return {key: _exact(value[key]) for key in sorted(value)}
    if isinstance(value, set):
        return sorted(value)
    if isinstance(value, (list, tuple)):
        return [_exact(item) for item in value]
    return value


def _day_payload(player) -> str:
    """Canonical exact snapshot of one simulated day."""
    plots = []
    for plot in player.plots:
        crop = plot.crop
        plots.append(
            [
                [_exact(getattr(plot, name)) for name in PLOT_FIELDS],
                plot.previous_crop_family,
                None
                if crop is None
                else [
                    crop.crop_id,
                    crop.day_planted,
                    crop.growth_days_required,
                    crop.last_watered_day,
                    crop.neglect_days,
                    crop.fertilized,
                    _exact(crop.accrued_cost),
                    [_exact(getattr(crop, name)) for name in STRESS_FIELDS],
                ],
            ]
        )
    snapshot = [
        player.day,
        _exact(player.money),
        _exact(player.total_revenue),
        _exact(player.total_expenses),
        _exact(player.reputation),
        player.bankrupt,
        player.slots_total,
        len(player.planted),
        player.fertilizer_inventory,
        _exact(player.water_units),
        sorted(player.upgrades_owned),
        _exact(player.market_prices),
        _exact(player.market_supply),
        _exact(player.crop_inventory),
        _exact(player.seed_inventory),
        [
            [lot.item_id, lot.quantity, lot.quality, lot.age_days, _exact(lot.unit_cost)]
            for lot in player.inventory_lots
        ],
        plots,
    ]
    return json.dumps(snapshot, sort_keys=True, separators=(",", ":"))


class _Trajectory:
    """Chained per-day digest of a run.

    Chained rather than a digest of the concatenation so that `trace` prints a
    per-day list in which the first differing entry is the first day that
    actually diverged -- every later entry differs too, by construction.
    """

    def __init__(self):
        self.running = b""
        self.per_day: list[str] = []

    def __call__(self, player) -> None:
        payload = _day_payload(player).encode("utf-8")
        self.running = hashlib.blake2b(self.running + payload, digest_size=8).digest()
        self.per_day.append(self.running.hex())

    @property
    def digest(self) -> str:
        return self.running.hex()


def _run_one(strategy_name: str, seed: int) -> tuple[dict, _Trajectory]:
    crops, upgrades, config, world = load_config()
    watering_settings, fertilizer_config = world["watering"], world["fertilizer"]
    agent = AGENT_REGISTRY[strategy_name]()
    trajectory = _Trajectory()
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
        on_day=trajectory,
    )
    stats = {
        field: _exact(getattr(player, field)) for field in TRACKED_FIELDS if field != "trajectory"
    }
    stats["trajectory"] = trajectory.digest
    return stats, trajectory


def _environment() -> dict:
    """Recorded alongside the baseline so a divergence can be attributed.

    Exact-float results are not guaranteed to be portable across interpreter
    versions -- CPython only gained compensated summation in sum() in 3.12, and
    simulation/crop_growth.py depends on it -- so a mismatch is worth checking
    against this before assuming the code changed.
    """
    try:
        from simulation import weather

        fastplot = weather._fastplot is not None
    except Exception:  # pragma: no cover - diagnostic only
        fastplot = None
    return {
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "platform": platform.platform(terse=True),
        "fastplot_active": fastplot,
    }


def _run_all() -> dict:
    results = {}
    for strategy_name in sorted(AGENT_REGISTRY):
        for seed in GOLDEN_SEEDS:
            key = f"{strategy_name}:{seed}"
            results[key], _trajectory = _run_one(strategy_name, seed)
    return results


def cmd_capture(_args: argparse.Namespace) -> int:
    results = _run_all()
    document = {"_meta": _environment(), "runs": results}
    BASELINE_PATH.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
    print(
        f"Captured baseline for {len(results)} (strategy, seed) combos "
        f"-> {BASELINE_PATH.relative_to(REPO_ROOT)}"
    )
    meta = document["_meta"]
    print(f"  {meta['implementation']} {meta['python']} on {meta['platform']}")
    print(f"  _fastplot active: {meta['fastplot_active']}")
    if meta["fastplot_active"]:
        print(
            "\nWARNING: captured with the C accelerator active. The pure-Python\n"
            "loop is the reference implementation -- capture with\n"
            "`python3 tools/build_fastplot.py --clean` so the baseline records\n"
            "the reference, then rebuild and `check` to verify the C agrees."
        )
    return 0


def cmd_check(_args: argparse.Namespace) -> int:
    if not BASELINE_PATH.exists():
        print(f"No baseline found at {BASELINE_PATH}. Run `capture` first.")
        return 2

    document = json.loads(BASELINE_PATH.read_text())
    baseline = document.get("runs")
    if baseline is None:
        print(
            f"Baseline at {BASELINE_PATH} predates the bit-exact format "
            "(floats were rounded to 6dp, and there was no trajectory hash).\n"
            "Re-run `capture` on pure Python to record a trustworthy baseline."
        )
        return 2
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

    recorded, now = document.get("_meta", {}), _environment()
    drifted = {k: (recorded.get(k), now.get(k)) for k in now if recorded.get(k) != now.get(k)}
    if drifted:
        print("\nEnvironment differs from the one that captured the baseline:")
        for name, (was, is_now) in sorted(drifted.items()):
            print(f"  {name}: {was!r} -> {is_now!r}")

    strategy, _, seed = failures[0][0].partition(":")
    print(
        f"\nBisect the first failure to a day:\n"
        f"  python3 {Path(__file__).name} trace {strategy} {seed}\n"
        "\nIf this is an intended behavior change, re-run `capture` and commit "
        "the updated golden_baseline.json alongside the change. Otherwise this "
        "is a determinism regression -- see SKILL.md."
    )
    return 1


def cmd_trace(args: argparse.Namespace) -> int:
    """Print per-day digests for one combo, against the baseline if present.

    The digests are chained, so the first line that differs is the first day
    that actually diverged; everything after it differs as a consequence.
    """
    if args.strategy not in AGENT_REGISTRY:
        print(f"Unknown strategy {args.strategy!r}. Known: {', '.join(sorted(AGENT_REGISTRY))}")
        return 2
    _stats, trajectory = _run_one(args.strategy, args.seed)

    baseline_days = None
    if BASELINE_PATH.exists():
        runs = json.loads(BASELINE_PATH.read_text()).get("runs", {})
        recorded = runs.get(f"{args.strategy}:{args.seed}", {}).get("trajectory")
        if recorded is not None and recorded == trajectory.digest:
            print(
                f"{args.strategy}:{args.seed} matches the baseline trajectory "
                f"({len(trajectory.per_day)} days, {recorded}). Nothing to bisect."
            )
            return 0
        baseline_days = recorded

    print(f"{args.strategy}:{args.seed} -- {len(trajectory.per_day)} simulated days")
    for day, digest in enumerate(trajectory.per_day, start=1):
        print(f"  day {day:>4}  {digest}")
    if baseline_days is not None:
        print(
            f"\nFinal digest {trajectory.digest} != baseline {baseline_days}.\n"
            "Re-run this on a known-good build and compare the two listings; "
            "the first differing day is where to look."
        )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("capture", help="Record current output as the committed baseline.")
    subparsers.add_parser("check", help="Diff current output against the committed baseline.")
    trace = subparsers.add_parser("trace", help="Print per-day digests for one combo.")
    trace.add_argument("strategy")
    trace.add_argument("seed", type=int)

    args = parser.parse_args()
    if args.command == "capture":
        return cmd_capture(args)
    if args.command == "trace":
        return cmd_trace(args)
    return cmd_check(args)


if __name__ == "__main__":
    raise SystemExit(main())
